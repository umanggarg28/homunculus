"""External-ingest routes — iOS Shortcut quick-capture and the generic webhook.

Both turn an inbound HTTP call into agent work: quick-capture runs a one-shot
agent to file a task/note from a phone; the webhook either creates an
immediately-due task or injects a message into the next conversation turn.

``Agent`` is resolved via ``wa.Agent`` (not a direct import) so the quick-capture
tests can patch ``web_api.Agent`` to stub the LLM round-trip.
"""

import os
import secrets
import time
from collections import defaultdict
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from homunculus.transports import web_api as wa

router = APIRouter()


# --- iOS Shortcuts quick-capture -------------------------------------------
# Lowest-latency way to add a task or note from a phone. The Shortcut
# (see docs/ios_shortcut.md) POSTs the user's spoken text here; the
# server feeds it to a one-shot agent with a narrow tool set and
# returns a short confirmation that Siri reads back.

_QUICK_CAPTURE_RATE: dict[str, list[float]] = defaultdict(list)
_QUICK_CAPTURE_RATE_WINDOW = 60.0
_QUICK_CAPTURE_RATE_MAX = 5

_QUICK_CAPTURE_PROMPT = """\
You received this message via the iOS Shortcut quick-capture endpoint:

> {text}

Kind hint from caller: {kind}

Decide between two actions:
- "task" → call create_task(title=..., description=..., due_at=...,
  recurrence="none|daily|weekly"). Parse natural-language times like
  "Friday at 3pm" into ISO using get_current_time() if needed.
- "note" → call archival_memory_insert(content=...) to save it as a
  searchable archival entry.

After the tool call, reply with EXACTLY ONE short line (≤ 80 chars)
confirming what you did — this is what Siri reads back to the user.
Examples:
  "Created task: Call dentist, Friday 15:00 IST."
  "Saved note about the book recommendation."
"""


def require_quick_capture_token(
    x_capture_token: str = Header(default=""),
) -> None:
    if not wa.QUICK_CAPTURE_TOKEN:
        raise HTTPException(503, "quick-capture is not configured on this server")
    if not secrets.compare_digest(x_capture_token, wa.QUICK_CAPTURE_TOKEN):
        raise HTTPException(401, "Invalid or missing X-Capture-Token")


def _check_quick_capture_rate(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    bucket = _QUICK_CAPTURE_RATE[ip]
    _QUICK_CAPTURE_RATE[ip] = [t for t in bucket if now - t < _QUICK_CAPTURE_RATE_WINDOW]
    if len(_QUICK_CAPTURE_RATE[ip]) >= _QUICK_CAPTURE_RATE_MAX:
        raise HTTPException(429, "quick-capture rate limit exceeded — max 5/min")
    _QUICK_CAPTURE_RATE[ip].append(now)


@router.post("/api/quick-capture", dependencies=[Depends(require_quick_capture_token)])
async def quick_capture(request: Request) -> JSONResponse:
    """Single-turn agent call to create a task or note from a phone."""
    _check_quick_capture_rate(request)
    payload = await request.json()
    text = (payload or {}).get("text", "").strip()
    kind = ((payload or {}).get("kind") or "auto").strip()
    if not text:
        raise HTTPException(400, "missing 'text'")
    if len(text) > 2000:
        raise HTTPException(400, "text too long (max 2000 chars)")

    # Fresh agent — must NOT share history with the chat session.
    agent = wa.Agent(memory=wa._chat_memory)
    prompt = _QUICK_CAPTURE_PROMPT.format(text=text, kind=kind)
    try:
        reply = agent.chat(prompt, source="ios-shortcut")
    except Exception as e:
        return JSONResponse(
            {"ok": False, "reply": f"Quick capture failed: {type(e).__name__}"},
            status_code=500,
        )
    return JSONResponse({"ok": True, "reply": reply.strip()})


# --- Webhooks ---------------------------------------------------------------
# External services (GitHub, Gmail, IFTTT, cron jobs, etc.) POST here to
# trigger the agent. Two modes:
#   task   — creates a task that fires on the next heartbeat tick (default)
#   inject — queues a message that the Telegram/web bot drains before the
#            next user turn (use for time-sensitive events that need
#            immediate context in conversation)
#
# Auth: if HOMUNCULUS_WEBHOOK_SECRET is set, the caller must pass it as
# the X-Webhook-Secret header. Leave unset for localhost-only use.

_WEBHOOK_SECRET = os.environ.get("HOMUNCULUS_WEBHOOK_SECRET", "").strip()


@router.post("/api/webhook")
async def webhook(request: Request) -> JSONResponse:
    """Receive an external event and create a task or inject a message."""
    secret_header = request.headers.get("x-webhook-secret", "")
    if _WEBHOOK_SECRET and not secrets.compare_digest(secret_header, _WEBHOOK_SECRET):
        raise HTTPException(401, "Invalid or missing X-Webhook-Secret header")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Request body must be valid JSON") from None

    mode = body.get("mode", "task")
    source = str(body.get("source", "webhook"))[:40]
    message = str(body.get("message", "")).strip()
    task_title = str(body.get("task_title", "")).strip() or f"[{source}] incoming event"
    task_description = message or str(body.get("description", "")).strip()

    if mode == "inject":
        # Drop straight into the notification queue so the next conversation
        # turn picks it up as context, without creating a persistent task.
        if not message:
            raise HTTPException(400, "'message' is required for mode=inject")
        wa._chat_memory.notifications.queue(f"[{source}] {message}")
        return JSONResponse({"ok": True, "mode": "inject", "source": source})

    # Default: create a task due immediately so the heartbeat fires it.
    now_iso = datetime.now().isoformat(timespec="seconds")
    task = wa._task_store().create(
        title=task_title,
        description=task_description,
        due_at=now_iso,
        recurrence="none",
        notify=True,
    )
    import homunculus.events as _events
    _events.emit("webhook_received", name=source, text=task_title, result=task["id"])
    return JSONResponse({"ok": True, "mode": "task", "task_id": task["id"], "source": source})
