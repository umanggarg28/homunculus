"""Chat routes — the interactive conversation surface and its sidebar signals.

The streaming send is the one stateful handler in the web layer: it holds the
shared chat agent + its lock for the turn's lifetime and journals the session on
exit. Cross-turn helpers that the tests pin at the web_api path
(``_visible_chat_history``) and those shared with other routers
(``_check_chat_rate``, ``_format_sse_data``) stay in web_api and are reached via
``wa.*``; the chat-only stream bookkeeping lives here.
"""

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from homunculus.core import Agent, MODEL
from homunculus.memory import Memory
from homunculus.transcript import Transcript
from homunculus.transports import web_api as wa

router = APIRouter()


@router.get("/api/input-expected", dependencies=[Depends(wa.require_web_auth)])
def input_expected() -> JSONResponse:
    """Whether the agent is waiting on input from the user — drives the CHAT
    sidebar badge. Currently the one persisted 'your turn' state is an
    unanswered quiz question (the agent asked, awaits your answer)."""
    try:
        from homunculus.quiz import _store
        p = _store()._load().get("pending") or {}
        # Only a DELIVERED question is "your turn" — a pending whose delivery
        # failed (notify timeout) must not light the badge for a question the
        # user never saw. Missing `delivered` (pre-fix orphan) reads as False.
        pending = p.get("topic") if p.get("delivered") else None
    except Exception:
        pending = None
    if pending:
        return JSONResponse({"expected": True, "reason": "quiz", "detail": pending})
    return JSONResponse({"expected": False, "reason": None, "detail": None})


@router.get("/api/notifications", dependencies=[Depends(wa.require_web_auth)])
def notifications_recent(limit: int = 8) -> JSONResponse:
    """Most recent notify() deliveries, newest last. Read-only — does
    NOT advance the chat drain pointer, so it never steals context from
    the telegram/web bridges. Powers the landing transmissions feed."""
    mem = wa._chat_memory or Memory(wa.MEMORY_DIR)
    return JSONResponse(mem.notifications.recent(max(1, min(50, limit))))


@router.get("/api/chat/history", dependencies=[Depends(wa.require_web_auth)])
def chat_history() -> JSONResponse:
    """Return complete persisted user/assistant chat turns for UI hydration.

    Reads from the append-only Transcript (Letta pattern, PRs #110/#111)
    so mid-session compaction — which rewrites the agent's in-context
    pointer list to summary + tail — cannot truncate what the user sees.
    The transcript file records every message ever; we filter to the
    visible user/final-assistant turns here.

    Falls back to load_session for sessions that pre-date the transcript
    (one-time migration after the Agent runs once, restore_session
    backfills the transcript from session.json — see PR #111).
    """
    memory = wa._chat_memory or Memory(wa.MEMORY_DIR)
    transcript = Transcript(memory.transcript_path)
    records = transcript.all()
    if records:
        # Stash the stable transcript IDs alongside each message so
        # _visible_chat_history can stamp them onto the visible turns
        # it emits — the existing filter already drops intermediate
        # tool-planning turns and orphaned user messages.
        msgs_with_ids = [{**msg, "_tx_id": rid} for rid, msg in records]
        out = wa._visible_chat_history(msgs_with_ids)
        for entry in out:
            tx_id = entry.pop("_source_tx_id", None)
            if tx_id is not None:
                entry["id"] = f"tx-{tx_id}"
        return JSONResponse(out)
    # Legacy path: session from before the transcript existed.
    return JSONResponse(wa._visible_chat_history(memory.load_session()))


# Stream cancellation state.
#   - _active_streams: currently-running stream IDs (so cancel can validate)
#   - _cancelled_streams: stream IDs whose owner asked us to stop
# A stream's generator checks `_cancelled_streams` between yielded chunks
# and exits cleanly with a [stopped] marker if its ID is in the set.
_active_streams: set[str] = set()
_cancelled_streams: set[str] = set()


def _drain_notifications_for_chat(agent) -> None:
    """Pull pending notifications into the web chat agent's history.
    Mirrors the same function in transports/telegram.py so a follow-up
    on the web ("explain it") has the notification text in context.
    """
    if getattr(agent, "memory", None) is None:
        return
    try:
        fresh = agent.memory.notifications.drain()
    except Exception as e:
        wa.log.warning("[web] notification drain failed: %s", e)
        return
    if not fresh:
        return
    from datetime import datetime as _dt
    for entry in fresh:
        text = entry.get("text", "")
        ts = entry.get("ts")
        try:
            when = _dt.fromtimestamp(float(ts)).strftime("%H:%M")
        except Exception:
            when = "earlier"
        agent.history.append({
            "role": "assistant",
            "content": f"[notification I sent you at {when}]\n\n{text}",
        })


# Curated set of model ids the user is most likely to want to swap to
# without typing the full provider path. Anything else is allowed too —
# this is just a discoverability list shown by bare `/use`.
_MODEL_HINTS = [
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "meta-llama/llama-3.3-70b-instruct:free",
    "openai/gpt-oss-120b:free",
    "anthropic/claude-sonnet-4-6",
    "anthropic/claude-haiku-4-5",
]


def _handle_use_command(agent: Agent, arg: str) -> str:
    if not arg:
        lines = [
            f"current model: `{agent.model}`",
            "",
            "switch with `/use <model-id>` — known options:",
        ]
        lines.extend(f"  - `{m}`" for m in _MODEL_HINTS)
        lines.append("")
        lines.append("`/use reset` returns to the default.")
        return "\n".join(lines)

    if arg == "reset":
        previous = agent.model
        agent.model = MODEL
        return f"model: `{previous}` -> `{agent.model}` (default)"

    previous = agent.model
    agent.model = arg
    return (
        f"model: `{previous}` -> `{agent.model}`. "
        "fallbacks still apply if this one rate-limits."
    )


@router.post("/api/chat/send", dependencies=[Depends(wa.require_web_auth)])
async def chat_send(request: Request):
    """SSE stream of the agent's reply to a posted user message.

    Accepts optional `stream_id` in the body — if provided, the client
    can call /api/chat/cancel with that ID to interrupt this stream
    mid-flight. The cancellation takes effect at the next chunk
    boundary (we can't interrupt a tool call already in progress)."""
    wa._check_chat_rate(request)
    payload = await request.json()
    user_message = (payload or {}).get("message", "").strip()
    if not user_message:
        raise HTTPException(400, "missing 'message'")

    stream_id = (payload or {}).get("stream_id") or secrets.token_urlsafe(12)
    agent = wa._get_chat_agent()

    # /use <model> — swap the primary model for this chat session without
    # restart. Empty arg shows current model + known catalogue. "reset"
    # restores the default. Fallbacks still kick in if the new primary
    # 429s, so a typo never bricks the chat.
    if user_message.startswith("/use"):
        reply = _handle_use_command(agent, user_message[4:].strip())

        def cmd_gen():
            yield wa._format_sse_data(reply)
            yield "event: done\ndata: end\n\n"

        return StreamingResponse(
            cmd_gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "X-Stream-Id": stream_id,
            },
        )

    def gen():
        _active_streams.add(stream_id)
        cancelled = False
        # Acquire the agent lock for the full stream lifetime so a
        # concurrent /api/chat/send cannot mutate history mid-turn.
        # Drain happens inside the lock too — it appends to history.
        wa._chat_agent_lock.acquire()
        reply_buf: list[str] = []
        try:
            _drain_notifications_for_chat(agent)
            for chunk in agent.chat_stream(user_message, source="web"):
                if stream_id in _cancelled_streams:
                    cancelled = True
                    break
                reply_buf.append(chunk)
                yield wa._format_sse_data(chunk)
        except Exception as e:
            msg = str(e)
            if "All providers exhausted" in msg or "token_quota_exceeded" in msg or "Tokens per minute" in msg:
                err_chunk = "[All AI providers are currently rate-limited. Wait a moment and try again.]"
            elif "All providers exhausted" in type(e).__name__ or "RuntimeError" in type(e).__name__ and "provider" in msg.lower():
                err_chunk = "[All AI providers are currently unavailable. Try again shortly.]"
            else:
                err_chunk = f"[error: {type(e).__name__}: {e}]"
            reply_buf.append(err_chunk)
            yield wa._format_sse_data(err_chunk)
        finally:
            _active_streams.discard(stream_id)
            _cancelled_streams.discard(stream_id)
            if cancelled:
                cancel_marker = "\n\n[stopped by user]"
                reply_buf.append(cancel_marker)
                yield wa._format_sse_data(cancel_marker)
            if wa._chat_memory is not None:
                wa._chat_memory.save_session(agent.history)
                # No longer writes _chat_log.jsonl — the Agent
                # journaled both turns into _transcript.jsonl as they
                # happened (PR #111), and /api/chat/history now reads
                # from there.
            wa._chat_agent_lock.release()
            yield "event: done\ndata: end\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Stream-Id": stream_id,
        },
    )


@router.post("/api/chat/cancel", dependencies=[Depends(wa.require_web_auth)])
async def chat_cancel(request: Request) -> JSONResponse:
    """Request cancellation of an in-flight chat stream."""
    payload = await request.json()
    stream_id = (payload or {}).get("stream_id", "").strip()
    if not stream_id:
        raise HTTPException(400, "missing 'stream_id'")
    if stream_id not in _active_streams:
        return JSONResponse({"ok": False, "reason": "stream not active"}, status_code=404)
    _cancelled_streams.add(stream_id)
    return JSONResponse({"ok": True})
