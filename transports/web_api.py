"""
Homunculus FastAPI service — JSON API + SPA static hosting.

All UI lives in /web (Vite + React + TypeScript). This module is
strictly:
  - JSON endpoints under /api/*
  - SSE stream at /events
  - Static file serving for the built SPA at /

There is intentionally no HTML, CSS, or JavaScript anywhere in this
file. UI presentation is fully decoupled from the API layer.
"""

import asyncio
import json
import os
import secrets
import threading
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles

import agent_controls
import tools
from core import Agent, API_URL, MODEL
from memory import Memory
from transcript import Transcript
from tasks import ALLOWED_RECURRENCE, TaskStore


# --- Config ---------------------------------------------------------------

EVENTS_PATH = Path(os.environ.get("HOMUNCULUS_EVENTS_PATH", "_events.jsonl"))
MEMORY_DIR = Path(os.environ.get("HOMUNCULUS_MEMORY_DIR", "./memory"))
TASKS_DIR = Path(os.environ.get("HOMUNCULUS_TASKS_DIR", "./tasks"))
SPA_DIST_DIR = Path(os.environ.get("HOMUNCULUS_WEB_DIST", "/app/web-dist"))
WEB_AUTH_TOKEN = os.environ.get("HOMUNCULUS_WEB_AUTH_TOKEN", "").strip()
# Dedicated token for the iOS Shortcut / quick-capture endpoint. Kept
# separate from the web auth token so a leaked Shortcut config can't
# also open the full dashboard. If unset, the endpoint refuses.
QUICK_CAPTURE_TOKEN = os.environ.get("HOMUNCULUS_QUICK_CAPTURE_TOKEN", "").strip()

POLL_INTERVAL = 0.25
# Minimum number of "real" (non-infra-ping) events that the SSE initial
# tail should contain, so the browser always has recent agent activity
# to show when it first connects. The tail scans back from EOF until it
# has this many real events, capped at INITIAL_TAIL_MAX raw lines.
INITIAL_TAIL_MIN_REAL = 30
INITIAL_TAIL_MAX = 2000
# Events that count as "infra noise" (matches the UI's SYSTEM_EVENTS
# filter). When the tail is dominated by these, we keep scanning back
# until the user gets actual activity (turns, tool calls, replies).
SSE_INFRA_EVENTS = {
    "service_ping", "provider_cooled", "context_compacted",
    "budget_blocked", "agent_controls_updated",
}

# Per-service liveness thresholds for /api/status.
STATUS_IDLE_SECONDS = 12 * 60
STATUS_STALE_SECONDS = 60 * 60


async def _web_ping_loop() -> None:
    """Emit a service_ping event every 10 minutes so /api/status never goes stale."""
    import events as _ev
    while True:
        try:
            _ev.emit("service_ping", name="web", text="alive")
        except Exception:
            pass
        await asyncio.sleep(10 * 60)


@asynccontextmanager
async def _lifespan(app_: object):
    import events as _ev
    dropped = _ev.rotate(keep_days=14)
    if dropped:
        print(f"[web] rotated _events.jsonl: dropped {dropped} lines older than 14 days", flush=True)
    task = asyncio.create_task(_web_ping_loop())
    yield
    task.cancel()


app = FastAPI(title="Homunculus API", lifespan=_lifespan)

# Memory + tools are initialised eagerly at process start so the
# /skills endpoint (and anything else that introspects tools.SCHEMAS)
# returns the real catalog before any chat has happened. The Agent
# itself is still lazy — it requires session-restoration which is
# cheaper to do on first chat call.
_chat_memory: Memory = Memory(MEMORY_DIR)
# Treat browser chat like Telegram/heartbeat: no interactive stdin,
# so shell_exec is disabled rather than hanging on a prompt.
tools.init(_chat_memory, autonomous=True)
_chat_agent: Agent | None = None
# Serialises mutations to the shared _chat_agent (history append, tool
# results, memory writes). Without this, two concurrent /api/chat/send
# streams race on the same history list and reply with corrupted /
# cross-mixed content. Held for the full duration of a chat stream —
# concurrent requests queue rather than interleave. Single-user app, so
# head-of-line blocking is acceptable; correctness > throughput.
_chat_agent_lock = threading.Lock()


def _get_chat_agent() -> Agent:
    global _chat_agent
    if _chat_agent is None:
        _chat_agent = Agent(memory=_chat_memory)
        _chat_agent.restore_session()
    return _chat_agent


def require_web_auth(
    x_homunculus_token: str = Header(default=""),
    token: str = Query(default=""),
) -> None:
    """Optional bearer-lite auth for local web/API access.

    Empty HOMUNCULUS_WEB_AUTH_TOKEN keeps localhost development frictionless.
    Once set, requests must send X-Homunculus-Token. The `token` query
    parameter exists for EventSource, which cannot set custom headers.
    """
    if not WEB_AUTH_TOKEN:
        return
    candidate = x_homunculus_token or token
    if not secrets.compare_digest(candidate, WEB_AUTH_TOKEN):
        raise HTTPException(401, "Invalid or missing Homunculus web token")


# --- API: status ----------------------------------------------------------

@app.get("/api/config")
def config() -> JSONResponse:
    return JSONResponse({"auth_required": bool(WEB_AUTH_TOKEN)})


@app.post("/api/user-tz")
async def user_tz_set(request: Request) -> JSONResponse:
    """Persist the browser-detected timezone so heartbeat and agent tools
    can use it. Called by the web UI on first load.

    Body: {"tz": "Asia/Kolkata"} — an IANA timezone name.
    Invalid names are silently ignored (better than 4xx-ing the user's
    perfectly normal session for a TZ we can't parse).
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "reason": "invalid json"}, status_code=400)
    tz = (body or {}).get("tz") if isinstance(body, dict) else None
    if not isinstance(tz, str) or not tz:
        return JSONResponse({"ok": False, "reason": "missing tz"}, status_code=400)
    try:
        from user_tz import set_user_tz_name, get_user_tz_name
        set_user_tz_name(tz)
        return JSONResponse({"ok": True, "stored": get_user_tz_name()})
    except Exception as e:
        return JSONResponse({"ok": False, "reason": str(e)}, status_code=500)


@app.get("/api/user-tz")
def user_tz_get() -> JSONResponse:
    """Return the currently stored user TZ (for debugging / UI display)."""
    try:
        from user_tz import get_user_tz_name
        return JSONResponse({"tz": get_user_tz_name()})
    except Exception as e:
        return JSONResponse({"tz": "UTC", "error": str(e)})


@app.get("/api/model")
def model_info() -> JSONResponse:
    """Return the model that actually handled the last request.

    Scans events.jsonl for the most recent llm_call event so the UI
    shows the real provider (e.g. kimi-k2.6:free when Gemini is down)
    rather than always showing the configured primary.
    Falls back to the configured primary if no events exist yet.
    """
    from urllib.parse import urlparse

    last_model = MODEL
    last_host = ""
    try:
        last_host = urlparse(API_URL).netloc
    except Exception:
        last_host = API_URL

    if EVENTS_PATH.exists():
        try:
            with EVENTS_PATH.open("r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("event") == "llm_call" and rec.get("model"):
                    last_model = rec["model"]
                    last_host = rec.get("host", last_host)
                    break
        except OSError:
            pass

    return JSONResponse({
        "model": last_model,
        "host": last_host,
        "primary": MODEL,
        "is_fallback": last_model != MODEL,
    })


@app.get("/api/status", dependencies=[Depends(require_web_auth)])
def status() -> JSONResponse:
    """Per-service liveness inferred from event-stream freshness."""
    services = ["heartbeat", "telegram", "web"]
    last_seen: dict[str, float | None] = {s: None for s in services}

    if EVENTS_PATH.exists():
        with EVENTS_PATH.open("r", encoding="utf-8") as f:
            tail = f.readlines()[-2000:]
        for line in tail:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            svc = rec.get("service")
            ts = rec.get("ts")
            if svc not in last_seen or not ts:
                continue
            try:
                t = datetime.fromisoformat(ts).timestamp()
            except ValueError:
                continue
            if last_seen[svc] is None or t > last_seen[svc]:
                last_seen[svc] = t

    now = datetime.now().timestamp()
    result: dict[str, dict] = {}
    for svc, t in last_seen.items():
        if t is None:
            result[svc] = {"state": "unknown", "last_seen": None, "age_s": None}
            continue
        age = int(now - t)
        if age < STATUS_IDLE_SECONDS:
            state = "live"
        elif age < STATUS_STALE_SECONDS:
            state = "idle"
        else:
            state = "stale"
        result[svc] = {"state": state, "last_seen": int(t), "age_s": age}
    return JSONResponse(result)


# --- API: memory ----------------------------------------------------------

@app.get("/api/memory", dependencies=[Depends(require_web_auth)])
def memory_list() -> JSONResponse:
    return JSONResponse(_list_memory_entries())


@app.get(
    "/api/memory/{filename}/raw",
    response_class=PlainTextResponse,
    dependencies=[Depends(require_web_auth)],
)
def memory_entry_raw(filename: str) -> PlainTextResponse:
    safe = _safe_subpath(filename, MEMORY_DIR)
    if safe is None or not safe.exists() or not safe.is_file():
        raise HTTPException(404, "Memory entry not found")
    return PlainTextResponse(safe.read_text(encoding="utf-8"))


@app.put(
    "/api/memory/{filename}/raw",
    dependencies=[Depends(require_web_auth)],
)
async def memory_entry_update(filename: str, request: Request) -> JSONResponse:
    """Overwrite a memory entry's raw markdown. Used by inline edit."""
    safe = _safe_subpath(filename, MEMORY_DIR)
    if safe is None:
        raise HTTPException(400, "invalid path")
    if not safe.exists() or not safe.is_file():
        raise HTTPException(404, "Memory entry not found")
    body = (await request.body()).decode("utf-8")
    # Refuse zero-length writes; the agent or user should use delete instead.
    if not body.strip():
        raise HTTPException(400, "empty body — use DELETE to remove an entry")
    safe.write_text(body, encoding="utf-8")
    return JSONResponse({"ok": True, "bytes": len(body.encode("utf-8"))})


@app.delete(
    "/api/memory/{filename}",
    dependencies=[Depends(require_web_auth)],
)
def memory_entry_delete(filename: str) -> JSONResponse:
    """Remove a memory entry — file + MEMORY.md index pointer."""
    memory = _chat_memory or Memory(MEMORY_DIR)
    result = memory.forget(filename)
    if "not found" in result.lower():
        raise HTTPException(404, result)
    return JSONResponse({"ok": True, "message": result})


# --- API: chapters --------------------------------------------------------

CHAPTERS_DIR = MEMORY_DIR / "_chapters"


@app.get("/api/chapters", dependencies=[Depends(require_web_auth)])
def chapters_list() -> JSONResponse:
    if not CHAPTERS_DIR.exists():
        return JSONResponse([])
    items = []
    for p in sorted(CHAPTERS_DIR.glob("*.json"), reverse=True):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        items.append({
            "id": p.stem,
            "title": data.get("title", p.stem),
            "opened_at": data.get("opened_at"),
            "closed_at": data.get("closed_at"),
            "turns": len(data.get("messages", [])),
        })
    return JSONResponse(items)


@app.post("/api/chapters/close", dependencies=[Depends(require_web_auth)])
def chapter_close() -> JSONResponse:
    """Archive the open chapter and start a fresh session.

    Title is derived from the first user message of the session, or the
    closing timestamp if none. The agent's living `history` is reset so
    the next reply starts from a clean slate; long-term memory is unaffected.
    """
    memory = _chat_memory or Memory(MEMORY_DIR)
    history = memory.load_session()
    if not history:
        raise HTTPException(400, "No open chapter to close.")

    CHAPTERS_DIR.mkdir(parents=True, exist_ok=True)
    closed_at = datetime.now().isoformat(timespec="seconds")
    chapter_id = closed_at.replace(":", "-")

    first_user = next(
        (m.get("content", "") for m in history if m.get("role") == "user"),
        "",
    )
    title = first_user.strip().split("\n", 1)[0][:80] or f"chapter of {closed_at[:10]}"

    payload = {
        "id": chapter_id,
        "title": title,
        "opened_at": history[0].get("ts") if history else closed_at,
        "closed_at": closed_at,
        "messages": history,
    }
    (CHAPTERS_DIR / f"{chapter_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    memory.clear_session()
    memory.clear_transcript()
    if _chat_agent is not None:
        _chat_agent.reset()

    return JSONResponse({"ok": True, "id": chapter_id, "title": title})


# --- API: mode (plan/build) -----------------------------------------------

@app.get("/api/mode", dependencies=[Depends(require_web_auth)])
def mode_get() -> JSONResponse:
    return JSONResponse({"mode": tools.get_mode()})


@app.post("/api/mode", dependencies=[Depends(require_web_auth)])
async def mode_set(request: Request) -> JSONResponse:
    body = await request.json()
    mode = (body or {}).get("mode")
    if mode not in {"plan", "build"}:
        raise HTTPException(400, "mode must be 'plan' or 'build'")
    tools.set_mode(mode)
    return JSONResponse({"mode": tools.get_mode()})


# --- API: agent controls + replay -----------------------------------------

@app.get("/api/agent/controls", dependencies=[Depends(require_web_auth)])
def agent_controls_get() -> JSONResponse:
    controls = agent_controls.load_controls().to_dict()
    controls["mode"] = tools.get_mode()
    return JSONResponse(controls)


@app.put("/api/agent/controls", dependencies=[Depends(require_web_auth)])
async def agent_controls_update(request: Request) -> JSONResponse:
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "body must be a JSON object")
    if "mode" in body:
        mode = body.get("mode")
        if mode not in {"plan", "build"}:
            raise HTTPException(400, "mode must be 'plan' or 'build'")
        tools.set_mode(mode)
    controls = agent_controls.save_controls(body).to_dict()
    controls["mode"] = tools.get_mode()
    import events as _events
    _events.emit(
        "agent_controls_updated",
        name="agent_controls",
        result=json.dumps(controls, sort_keys=True),
    )
    return JSONResponse(controls)


@app.get("/api/agent/replay", dependencies=[Depends(require_web_auth)])
def agent_replay(limit: int = 12) -> JSONResponse:
    return JSONResponse(_build_agent_replay(limit=max(1, min(limit, 50))))


# --- API: skills (tools) --------------------------------------------------

@app.get("/api/skills", dependencies=[Depends(require_web_auth)])
def skills_list() -> JSONResponse:
    """Aggregate per-tool stats from tools.SCHEMAS + _events.jsonl.

    Used by the Skills page to answer "what does this agent reliably do?"
    — name + description + call count + success rate + last used.
    """
    by_name: dict[str, dict] = {}
    for schema in tools.SCHEMAS:
        fn = schema.get("function", {})
        name = fn.get("name")
        if not name:
            continue
        by_name[name] = {
            "name": name,
            "description": fn.get("description", ""),
            "call_count": 0,
            "success_count": 0,
            "failure_count": 0,
            "last_used": None,
            "last_status": None,
            "recent_calls": [],  # ISO ts of tool_call events in the last 24h
        }

    if EVENTS_PATH.exists():
        with EVENTS_PATH.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("event") not in {"tool_call", "tool_result"}:
                    continue
                name = rec.get("name")
                entry = by_name.get(name)
                if entry is None:
                    continue
                ts = rec.get("ts")
                if rec["event"] == "tool_call":
                    entry["call_count"] += 1
                    if ts:
                        if entry["last_used"] is None or ts > entry["last_used"]:
                            entry["last_used"] = ts
                        entry["recent_calls"].append(ts)
                else:  # tool_result
                    result = rec.get("result") or ""
                    is_failure = result.lstrip().startswith("ERROR")
                    if is_failure:
                        entry["failure_count"] += 1
                        status = "failure"
                    else:
                        entry["success_count"] += 1
                        status = "success"
                    if ts and (entry["last_used"] is None or ts >= entry["last_used"]):
                        entry["last_used"] = ts
                        entry["last_status"] = status

    # Trim recent_calls to last 24h to keep the payload small.
    cutoff_dt = datetime.now()
    cutoff_iso = (cutoff_dt - timedelta(days=1)).isoformat(timespec="seconds")
    for entry in by_name.values():
        entry["recent_calls"] = [t for t in entry["recent_calls"] if t >= cutoff_iso]
        entry["uses"] = None
        entry["consecutive_failures"] = None

    # Overlay uses/consecutive_failures from skill_*.md frontmatter (written by rate_skill).
    # These track agent-learned procedures — distinct from MCP tool call counts above.
    mem_dir = MEMORY_DIR if MEMORY_DIR.exists() else None
    if mem_dir:
        import re as _re
        for skill_file in mem_dir.glob("skill_*.md"):
            try:
                text = skill_file.read_text(encoding="utf-8")
            except OSError:
                continue
            # Extract skill name from frontmatter `name:` field to match against by_name.
            name_m = _re.search(r"^name:\s*(.+)$", text, _re.MULTILINE)
            if not name_m:
                continue
            skill_name = name_m.group(1).strip()
            entry = by_name.get(skill_name)
            if entry is None:
                continue
            uses_m = _re.search(r"^uses:\s*(\d+)", text, _re.MULTILINE)
            cf_m = _re.search(r"^consecutive_failures:\s*(\d+)", text, _re.MULTILINE)
            if uses_m:
                entry["uses"] = int(uses_m.group(1))
            if cf_m:
                entry["consecutive_failures"] = int(cf_m.group(1))

    return JSONResponse(list(by_name.values()))


# --- API: tasks -----------------------------------------------------------

def _task_store() -> TaskStore:
    return TaskStore(TASKS_DIR)


@app.get("/api/tasks", dependencies=[Depends(require_web_auth)])
def tasks_list(status: str = "all") -> JSONResponse:
    """List tasks. status = active | completed | cancelled | all."""
    try:
        items = _task_store().list(status=status)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return JSONResponse(items)


@app.post("/api/tasks", dependencies=[Depends(require_web_auth)])
async def tasks_create(request: Request) -> JSONResponse:
    body = await request.json()
    title = (body or {}).get("title", "").strip()
    if not title:
        raise HTTPException(400, "title is required")
    try:
        task = _task_store().create(
            title=title,
            description=body.get("description", ""),
            due_at=body.get("due_at"),
            recurrence=body.get("recurrence", "none"),
            notify=body.get("notify", False),
            success_criteria=body.get("success_criteria"),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return JSONResponse(task)


@app.patch("/api/tasks/{task_id}", dependencies=[Depends(require_web_auth)])
async def tasks_update(task_id: str, request: Request) -> JSONResponse:
    body = await request.json() or {}
    try:
        task = _task_store().update(
            task_id,
            title=body.get("title"),
            description=body.get("description"),
            due_at=body.get("due_at"),
            recurrence=body.get("recurrence"),
            notify=body.get("notify"),
            success_criteria=body.get("success_criteria"),
        )
    except KeyError:
        raise HTTPException(404, f"task '{task_id}' not found")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return JSONResponse(task)


@app.post("/api/tasks/{task_id}/complete", dependencies=[Depends(require_web_auth)])
async def tasks_complete(task_id: str, request: Request) -> JSONResponse:
    body = (await request.json()) if request.headers.get("content-length") else {}
    try:
        task = _task_store().complete(task_id, result=(body or {}).get("result", ""))
    except KeyError:
        raise HTTPException(404, f"task '{task_id}' not found")
    return JSONResponse(task)


@app.post("/api/tasks/{task_id}/cancel", dependencies=[Depends(require_web_auth)])
async def tasks_cancel(task_id: str, request: Request) -> JSONResponse:
    body = (await request.json()) if request.headers.get("content-length") else {}
    try:
        task = _task_store().cancel(task_id, reason=(body or {}).get("reason", ""))
    except KeyError:
        raise HTTPException(404, f"task '{task_id}' not found")
    return JSONResponse(task)


@app.post("/api/tasks/{task_id}/run-now", dependencies=[Depends(require_web_auth)])
def tasks_run_now(task_id: str) -> JSONResponse:
    """Legacy "schedule the task to fire on the next heartbeat tick" endpoint.

    For interactive streamed execution use POST /api/tasks/{task_id}/run-stream
    which actually runs the task right now and streams the events back.
    """
    try:
        task = _task_store().run_now(task_id)
    except KeyError:
        raise HTTPException(404, f"task '{task_id}' not found")
    return JSONResponse(task)


@app.post("/api/tasks/{task_id}/run-stream", dependencies=[Depends(require_web_auth)])
async def tasks_run_stream(task_id: str, request: Request):
    """Run a single task right now and stream the agent's execution as SSE.

    The agent is fresh (NOT the chat agent — task execution is isolated, like
    a heartbeat tick is). TaskGuard installs the same success_criteria guard
    we use during scheduled runs. The post-execution housekeeping (record
    failure / advance due_at) is identical to heartbeat.tick().

    This is T1.4 of docs/CAPABILITY_ROADMAP.md — the "click ARMED → stream
    in place" UX that removes the trip to Traces for ad-hoc task runs.
    """
    _check_chat_rate(request)
    store = _task_store()
    task = store.get(task_id)
    if task is None:
        raise HTTPException(404, f"task '{task_id}' not found")
    if task.get("status") != "active":
        raise HTTPException(409, f"task is {task.get('status')} — only active tasks can be run")

    # Import lazily to avoid pulling heartbeat at module-import time.
    from heartbeat import HEARTBEAT_PROMPT_TEMPLATE, TaskGuard, _format_due_tasks
    from user_tz import now_user_tz

    # Stamp last_fired_at and executing=True before we start so concurrent
    # heartbeat ticks don't pick up the same task. Heartbeat's 30-min
    # suppression window does the rest.
    store.mark_fired(task_id)

    # Fresh agent — task execution must NOT share history with the chat
    # session (would pollute future chat turns with task-execution noise).
    fresh_agent = Agent(memory=_chat_memory)
    prompt = HEARTBEAT_PROMPT_TEMPLATE.format(
        now_iso=now_user_tz().isoformat(timespec="seconds"),
        due_tasks=_format_due_tasks([task]),
    )

    guard = TaskGuard({task_id: task.get("success_criteria") or []})
    tools.set_pre_execute_hook(guard.on_tool_call)
    tools.set_pre_turn_hook(guard.on_pre_turn)
    from datetime import timezone as _tz
    from core import measure_llm_usage_since
    due_at_before = task.get("due_at")
    started_iso = datetime.now().isoformat(timespec="seconds")
    started_utc = datetime.now(_tz.utc)

    def gen():
        try:
            yield _format_sse_data(f"[run-now started at {started_iso}]")
            try:
                for chunk in fresh_agent.chat_stream(prompt):
                    yield _format_sse_data(chunk)
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
                yield _format_sse_data(f"[loop error: {err}]")
                try:
                    current = store.get(task_id)
                    if current and current.get("due_at") == due_at_before:
                        store.record_failure(
                            task_id, err, increment_failures=False,
                            usage=measure_llm_usage_since(started_utc),
                        )
                except Exception:
                    pass
                yield "event: done\ndata: end\n\n"
                return

            # Post-success check: did due_at advance? If yes, complete_task
            # ran. If no, the agent silently dropped — mark partial so
            # the scratchpad survives and the next attempt resumes.
            usage = measure_llm_usage_since(started_utc)
            current = store.get(task_id)
            if current and current.get("due_at") == due_at_before and task_id in guard.expected_remaining():
                store.mark_partial(
                    task_id,
                    "run-now: agent finished without complete_task / "
                    "continue_task / cancel_task",
                    usage=usage,
                )
                yield _format_sse_data("[silent drop — marked partial, will resume next tick]")
            else:
                # complete_task ran — retrofit usage onto the success run.
                store.attribute_usage_to_last_run(task_id, usage)
                yield _format_sse_data("[run-now finished]")
        finally:
            tools.set_pre_execute_hook(None)
            tools.set_pre_turn_hook(None)
            yield "event: done\ndata: end\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.delete("/api/tasks/{task_id}", dependencies=[Depends(require_web_auth)])
def tasks_delete(task_id: str) -> JSONResponse:
    try:
        _task_store().delete(task_id)
    except KeyError:
        raise HTTPException(404, f"task '{task_id}' not found")
    return JSONResponse({"ok": True})


@app.get("/api/tasks/meta", dependencies=[Depends(require_web_auth)])
def tasks_meta() -> JSONResponse:
    """Constants the form UI needs (allowed recurrence values, etc.)."""
    return JSONResponse({
        "recurrence_options": sorted(ALLOWED_RECURRENCE),
    })


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
    if not QUICK_CAPTURE_TOKEN:
        raise HTTPException(503, "quick-capture is not configured on this server")
    if not secrets.compare_digest(x_capture_token, QUICK_CAPTURE_TOKEN):
        raise HTTPException(401, "Invalid or missing X-Capture-Token")


def _check_quick_capture_rate(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    bucket = _QUICK_CAPTURE_RATE[ip]
    _QUICK_CAPTURE_RATE[ip] = [t for t in bucket if now - t < _QUICK_CAPTURE_RATE_WINDOW]
    if len(_QUICK_CAPTURE_RATE[ip]) >= _QUICK_CAPTURE_RATE_MAX:
        raise HTTPException(429, "quick-capture rate limit exceeded — max 5/min")
    _QUICK_CAPTURE_RATE[ip].append(now)


@app.post("/api/quick-capture", dependencies=[Depends(require_quick_capture_token)])
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
    agent = Agent(memory=_chat_memory)
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


@app.post("/api/webhook")
async def webhook(request: Request) -> JSONResponse:
    """Receive an external event and create a task or inject a message."""
    secret_header = request.headers.get("x-webhook-secret", "")
    if _WEBHOOK_SECRET and not secrets.compare_digest(secret_header, _WEBHOOK_SECRET):
        raise HTTPException(401, "Invalid or missing X-Webhook-Secret header")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Request body must be valid JSON")

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
        _chat_memory.notifications.queue(f"[{source}] {message}")
        return JSONResponse({"ok": True, "mode": "inject", "source": source})

    # Default: create a task due immediately so the heartbeat fires it.
    now_iso = datetime.now().isoformat(timespec="seconds")
    task = _task_store().create(
        title=task_title,
        description=task_description,
        due_at=now_iso,
        recurrence="none",
        notify=True,
    )
    import events as _events
    _events.emit("webhook_received", name=source, text=task_title, result=task["id"])
    return JSONResponse({"ok": True, "mode": "task", "task_id": task["id"], "source": source})


@app.get("/api/agent/upcoming", dependencies=[Depends(require_web_auth)])
def agent_upcoming() -> JSONResponse:
    """What the agent is set to do next.

    Returns:
      - next_tick: ISO datetime the heartbeat will fire (one-shot if
        scheduled, otherwise default-interval estimate from last tick),
      - default_interval_min: the heartbeat's fallback cadence,
      - next_task: earliest-due active task (id, title, due_at), if any.
    """
    mem = _chat_memory or Memory(MEMORY_DIR)
    interval_min = int(os.environ.get("HEARTBEAT_INTERVAL_MINUTES", "60"))
    explicit_tick = mem.next_tick.peek()

    # When no explicit tick is scheduled, fall back to estimate from last heartbeat event + interval.
    # The heartbeat now also writes its wake time to memory/_next_tick.txt while sleeping, so
    # explicit_tick covers both agent-scheduled and heartbeat-scheduled wakes.
    estimated_tick: str | None = explicit_tick
    if not estimated_tick and EVENTS_PATH.exists():
        last_hb_ts: float | None = None
        try:
            with EVENTS_PATH.open("r", encoding="utf-8", errors="replace") as f:
                for line in f.readlines()[-500:]:
                    try:
                        rec = json.loads(line)
                        if rec.get("service") == "heartbeat":
                            t = datetime.fromisoformat(rec["ts"]).timestamp()
                            if last_hb_ts is None or t > last_hb_ts:
                                last_hb_ts = t
                    except (json.JSONDecodeError, KeyError, ValueError):
                        continue
        except OSError:
            pass
        if last_hb_ts is not None:
            estimated = datetime.fromtimestamp(last_hb_ts) + timedelta(minutes=interval_min)
            if estimated > datetime.now():
                estimated_tick = estimated.isoformat(timespec="seconds")

    # Earliest active task by due_at.
    store = TaskStore(Path(os.environ.get("HOMUNCULUS_TASKS_DIR", "./tasks")))
    next_task = None
    earliest_iso: str | None = None
    for t in store.list("active"):
        due = t.get("due_at")
        if not due:
            continue
        if earliest_iso is None or due < earliest_iso:
            earliest_iso = due
            next_task = {"id": t["id"], "title": t["title"], "due_at": due,
                          "recurrence": t.get("recurrence", "none")}

    return JSONResponse({
        "next_tick": estimated_tick,
        "default_interval_min": interval_min,
        "next_task": next_task,
    })


# --- API: stats -----------------------------------------------------------

# Cost per million tokens (input, output) for known models.
# Models ending in ":free" are always $0. Anything not listed uses 0
# so we never overcount — better to undercount than show $2 for free runs.
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    # ($/1M input, $/1M output) — updated June 2026
    "gemini-2.5-flash":                         (0.15,   0.60),
    "gemini-2.5-pro":                           (1.25,  10.00),
    "gemini-2.0-flash":                         (0.10,   0.40),
    "llama-3.3-70b-versatile":                  (0.59,   0.79),
    "llama-3.1-8b-instant":                     (0.05,   0.08),
    "openai/gpt-4o":                            (2.50,  10.00),
    "openai/gpt-4o-mini":                       (0.15,   0.60),
    "openai/gpt-4.1-mini":                      (0.40,   1.60),
    "anthropic/claude-sonnet-4-6":              (3.00,  15.00),
    "anthropic/claude-haiku-4-5":               (1.00,   5.00),
    "deepseek/deepseek-v3":                     (0.14,   0.28),
}

def _model_cost_cents(model: str, input_tok: int, output_tok: int, cached_tok: int) -> float:
    """Return estimated cost in cents for one LLM call. Free models → 0."""
    if not model or model.endswith(":free"):
        return 0.0
    price_in, price_out = _MODEL_PRICING.get(model, (0.0, 0.0))
    uncached = max(0, input_tok - cached_tok)
    cached_in = cached_tok
    return (uncached * price_in + cached_in * price_in * 0.1 + output_tok * price_out) / 1_000_000 * 100


def _build_agent_replay(limit: int = 12) -> list[dict]:
    """Build recent inspectable turns from the append-only event log."""
    if not EVENTS_PATH.exists():
        return []
    turns: list[dict] = []
    current: dict | None = None
    try:
        with EVENTS_PATH.open("r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-5000:]
    except OSError:
        return []

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        event = rec.get("event")
        ts = rec.get("ts")
        service = rec.get("service", "")

        if event == "user_message":
            if current is not None:
                turns.append(current)
            current = _new_replay_turn(len(turns), ts, service, rec.get("text", ""))
            continue

        if current is None:
            if _starts_autonomous_replay(event, service):
                current = _new_replay_turn(
                    len(turns),
                    ts,
                    service,
                    _autonomous_replay_label(service, event, rec),
                )
            else:
                continue
        if ts:
            current["ended_at"] = ts

        if event == "assistant_reply":
            current["assistant"] = rec.get("text", "")
            current["ended_at"] = ts
            turns.append(current)
            current = None
            continue

        if event == "llm_call":
            in_tok = int(rec.get("input_tokens") or 0)
            out_tok = int(rec.get("output_tokens") or 0)
            cached = int(rec.get("cached_tokens") or 0)
            model = rec.get("model") or rec.get("name") or ""
            cost = _model_cost_cents(model, in_tok, out_tok, cached)
            current["input_tokens"] += in_tok
            current["output_tokens"] += out_tok
            current["cached_tokens"] += cached
            current["cost_cents"] += cost
            current["models"].append({
                "ts": ts,
                "model": model,
                "host": rec.get("host", ""),
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "cached_tokens": cached,
                "cost_cents": round(cost, 4),
                "request": rec.get("request", ""),
            })
            continue

        if event in {"tool_call", "tool_result", "tool_blocked"}:
            if event == "tool_call":
                current["tools"].append({
                    "name": rec.get("name", ""),
                    "args": rec.get("args", ""),
                    "result": "",
                    "status": "pending",
                    "started_at": ts,
                })
            else:
                name = rec.get("name", "")
                target = next(
                    (t for t in reversed(current["tools"]) if t.get("name") == name and t.get("status") == "pending"),
                    None,
                )
                if target is None:
                    target = {"name": name, "args": "", "started_at": ts}
                    current["tools"].append(target)
                result = rec.get("result", "")
                target["result"] = result
                target["ended_at"] = ts
                target["status"] = (
                    "blocked" if event == "tool_blocked"
                    else "failure" if str(result).lstrip().startswith("ERROR")
                    else "success"
                )
            continue

        if event in {
            "output_guard",
            "self_correction",
            "budget_blocked",
            "provider_cooled",
            "task_failure",
            "memory_write",
            "memory_forget",
        }:
            current["guards"].append({
                "ts": ts,
                "event": event,
                "name": rec.get("name", ""),
                "text": rec.get("text", "") or rec.get("description", "") or rec.get("memory_name", ""),
                "result": rec.get("result", "") or rec.get("action", ""),
            })

    if current is not None:
        turns.append(current)

    for turn in turns:
        turn["cost_cents"] = round(float(turn.get("cost_cents") or 0.0), 4)
    return turns[-limit:][::-1]


def _new_replay_turn(index: int, ts: str | None, service: str, user: str) -> dict:
    return {
        "id": f"turn-{index + 1}",
        "started_at": ts,
        "service": service,
        "user": user,
        "assistant": "",
        "models": [],
        "tools": [],
        "guards": [],
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_tokens": 0,
        "cost_cents": 0.0,
    }


def _starts_autonomous_replay(event: str | None, service: str) -> bool:
    if service in {"", "unknown"}:
        return False
    return event in {
        "llm_call",
        "tool_call",
        "tool_result",
        "tool_blocked",
        "output_guard",
        "self_correction",
        "budget_blocked",
        "provider_cooled",
        "task_failure",
        "memory_write",
        "memory_forget",
    }


def _autonomous_replay_label(service: str, event: str | None, rec: dict) -> str:
    if service == "heartbeat":
        if event == "task_failure":
            name = rec.get("name") or "scheduled task"
            return f"Autonomous heartbeat: task failure in {name}"
        return "Autonomous heartbeat tick"
    if event in {"memory_write", "memory_forget"}:
        name = rec.get("memory_name") or rec.get("name") or "memory"
        return f"{service}: {event.replace('_', ' ')} · {name}"
    if event in {"budget_blocked", "provider_cooled"}:
        name = rec.get("name") or "provider"
        return f"{service}: {event.replace('_', ' ')} · {name}"
    return f"{service}: autonomous {event or 'event'}"


@app.get("/api/stats/today", dependencies=[Depends(require_web_auth)])
def stats_today() -> JSONResponse:
    """Return activity counts since the user's local midnight today.

    Windows on the *user's* timezone (not UTC) so the budget visibly
    resets when the user's calendar day flips, not at 05:30 IST.
    Falls back to UTC if user_tz isn't set yet.
    """
    from datetime import timezone
    try:
        from user_tz import get_user_tz_name
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(get_user_tz_name())
        local_midnight = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
        cutoff = local_midnight.astimezone(timezone.utc)
    except Exception:
        cutoff = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    events_path = Path(os.environ.get("HOMUNCULUS_EVENTS_PATH", "_events.jsonl"))
    total_events = 0
    unique_tools: set[str] = set()
    tasks_fired = 0
    memory_writes = 0
    memory_forgets = 0
    input_tokens = 0
    output_tokens = 0
    cached_tokens = 0
    cost_cents = 0.0

    if events_path.exists():
        try:
            with events_path.open("r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts_raw = rec.get("ts", "")
                try:
                    ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                if ts < cutoff:
                    break
                total_events += 1
                evt = rec.get("event", "")
                if evt == "tool_call":
                    name = rec.get("name") or ""
                    if name:
                        unique_tools.add(name)
                    if name == "complete_task":
                        tasks_fired += 1
                elif evt == "memory_write":
                    memory_writes += 1
                elif evt == "memory_forget":
                    memory_forgets += 1
                elif evt == "llm_call":
                    in_tok = rec.get("input_tokens") or 0
                    out_tok = rec.get("output_tokens") or 0
                    ca_tok = rec.get("cached_tokens") or 0
                    input_tokens += in_tok
                    output_tokens += out_tok
                    cached_tokens += ca_tok
                    cost_cents += _model_cost_cents(rec.get("model", ""), in_tok, out_tok, ca_tok)
        except OSError:
            pass

    budget_usd = float(os.environ.get("HOMUNCULUS_DAILY_BUDGET_USD", "0") or "0")
    return JSONResponse({
        "since": cutoff.isoformat(),
        "events": total_events,
        "unique_tools": len(unique_tools),
        "tasks_fired": tasks_fired,
        "memory_writes": memory_writes,
        "memory_forgets": memory_forgets,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_tokens": cached_tokens,
        "cost_cents": round(cost_cents, 2),
        "budget_cents": round(budget_usd * 100, 2),
    })


# --- API: context window gauge --------------------------------------------

# Known context limits for common model IDs (tokens).
_CONTEXT_LIMITS: dict[str, int] = {
    # Gemini
    "gemini-2.5-flash":              1_048_576,
    "gemini-2.5-pro":                1_048_576,
    "gemini-2.0-flash":              1_048_576,
    "gemini-1.5-flash":              1_048_576,
    "gemini-1.5-pro":                2_097_152,
    # OpenAI
    "gpt-4o":                          128_000,
    "gpt-4o-mini":                     128_000,
    "gpt-4-turbo":                     128_000,
    "gpt-4":                            32_768,
    "gpt-oss-120b":                    128_000,
    # Groq / Meta Llama
    "llama-3.3-70b-versatile":         128_000,
    "llama-3.3-70b-instruct":          131_072,
    "llama-3.2-3b-instruct":           131_072,
    # OpenRouter free fallbacks (verified June 2026)
    "kimi-k2":                         262_144,   # moonshotai/kimi-k2.6:free
    "qwen3-coder":                   1_048_576,   # qwen/qwen3-coder:free
    "qwen3-next":                      262_144,   # qwen/qwen3-next-80b-a3b-instruct:free
    "hermes-3":                        131_072,   # nousresearch/hermes-3-llama-3.1-405b:free
    "gemma-4":                         262_144,   # google/gemma-4-31b-it:free
    # DeepSeek / Anthropic
    "deepseek":                        163_840,
    "claude-haiku":                    200_000,
    "claude-sonnet":                   200_000,
}

def _context_limit_for(model: str) -> int:
    """Return the context window size for a model ID, falling back to 128k."""
    for key, limit in _CONTEXT_LIMITS.items():
        if key in model:
            return limit
    return 128_000


@app.get("/api/context", dependencies=[Depends(require_web_auth)])
def context_gauge() -> JSONResponse:
    """Return the latest prompt token count and model context limit.

    Scans _events.jsonl from the end for the most recent llm_call event
    that has input_tokens. The prompt_tokens value from the API response
    IS the full current context size (cumulative, not incremental).
    """
    events_path = Path(os.environ.get("HOMUNCULUS_EVENTS_PATH", "_events.jsonl"))
    last_input_tokens: int = 0
    last_model: str = os.environ.get("HOMUNCULUS_MODEL", "gemini-2.5-flash")

    if events_path.exists():
        try:
            with events_path.open("r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("event") == "llm_call" and rec.get("input_tokens"):
                    last_input_tokens = rec["input_tokens"]
                    if rec.get("model"):
                        last_model = rec["model"]
                    break
        except OSError:
            pass

    limit = _context_limit_for(last_model)
    return JSONResponse({
        "used_tokens": last_input_tokens,
        "limit_tokens": limit,
        "model": last_model,
        "pct": round(last_input_tokens / limit * 100, 1) if limit else 0,
    })


# --- API: logs ------------------------------------------------------------

@app.get("/api/logs", dependencies=[Depends(require_web_auth)])
def logs_list() -> JSONResponse:
    return JSONResponse(_list_log_files())


@app.get(
    "/api/logs/{rel:path}/raw",
    response_class=PlainTextResponse,
    dependencies=[Depends(require_web_auth)],
)
def log_entry_raw(rel: str) -> PlainTextResponse:
    logs_root = MEMORY_DIR / "logs"
    safe = _safe_subpath(rel, logs_root)
    if safe is None or not safe.exists() or not safe.is_file():
        raise HTTPException(404, "Log not found")
    return PlainTextResponse(safe.read_text(encoding="utf-8"))


# --- API: chat ------------------------------------------------------------

@app.get("/api/chat/history", dependencies=[Depends(require_web_auth)])
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
    memory = _chat_memory or Memory(MEMORY_DIR)
    transcript = Transcript(memory.transcript_path)
    records = transcript.all()
    if records:
        # Stash the stable transcript IDs alongside each message so
        # _visible_chat_history can stamp them onto the visible turns
        # it emits — the existing filter already drops intermediate
        # tool-planning turns and orphaned user messages.
        msgs_with_ids = [{**msg, "_tx_id": rid} for rid, msg in records]
        out = _visible_chat_history(msgs_with_ids)
        for entry in out:
            tx_id = entry.pop("_source_tx_id", None)
            if tx_id is not None:
                entry["id"] = f"tx-{tx_id}"
        return JSONResponse(out)
    # Legacy path: session from before the transcript existed.
    return JSONResponse(_visible_chat_history(memory.load_session()))


# Sources whose messages are agent-internal machinery, never chat turns:
#   heartbeat  — tick prompts and mid-run model text from autonomous runs
#   refinement — skill-refinement sessions
#   harness    — corrections the loop injects (detector retries, budget
#                nudges); they're role=user but the user never typed them
_NON_CHAT_SOURCES = frozenset({"heartbeat", "refinement", "harness"})


def _visible_chat_history(history: list[dict]) -> list[dict]:
    """Filter persisted agent history down to visible complete chat turns.

    If an input message carries `_tx_id` (from the transcript path), the
    output entry passes it through as `_source_tx_id` so the caller can
    rewrite the entry's id to a stable transcript-based form.
    """
    messages = []
    pending_user: dict | None = None
    for idx, msg in enumerate(history):
        role = msg.get("role")
        content = msg.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        # Agent-internal traffic (heartbeat tick prompts, harness
        # corrections, refinement runs) shares the transcript with chat
        # but is not part of the user's conversation. It used to render
        # as fake YOU/AI bubbles — the "traces leaking into chat" bug.
        if msg.get("source") in _NON_CHAT_SOURCES:
            continue
        # Skip heartbeat notifications — they live in LLM context for
        # follow-up questions but shouldn't appear as chat bubbles.
        if content.startswith("[notification I sent you at"):
            continue
        tx_id = msg.get("_tx_id")
        if role == "user":
            pending_user = {
                "id": f"persisted-{idx}",
                "role": role,
                "content": content,
                "source": msg.get("source", "web"),
                "ts": msg.get("ts"),
                "_raw_idx": idx,
            }
            if tx_id is not None:
                pending_user["_source_tx_id"] = tx_id
            continue
        if role == "assistant":
            # Providers may return visible-looking content together with
            # tool_calls, e.g. "Here is the table..." plus write_file().
            # That is an intermediate tool-planning message, not the final
            # user-facing reply. The final assistant message arrives after
            # the tool_result and has no tool_calls.
            if msg.get("tool_calls"):
                continue
            if pending_user is not None:
                messages.append(pending_user)
                pending_user = None
            entry = {
                "id": f"persisted-{idx}",
                "role": role,
                "content": content,
                "source": msg.get("source", "web"),
                "ts": msg.get("ts"),
                "_raw_idx": idx,
            }
            if tx_id is not None:
                entry["_source_tx_id"] = tx_id
            # Transcript rewrite pair: _journal_append wrote the raw
            # reply, _journal_replace_last_content appended the guard-
            # rewritten form right after it. The records are adjacent
            # and the LATER one is the final form. (Also collapses the
            # legacy duplicates the unconditional rewrite left on disk.)
            prev = messages[-1] if messages else None
            if (
                prev is not None
                and prev.get("role") == "assistant"
                and prev.get("_raw_idx") == idx - 1
            ):
                messages[-1] = entry
            else:
                messages.append(entry)
    for entry in messages:
        entry.pop("_raw_idx", None)
    return messages


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
        print(f"[web] notification drain failed: {e}", flush=True)
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


# Per-IP rate-limit state for /api/chat/send (in-memory, resets on restart).
_chat_rate: dict[str, list[float]] = defaultdict(list)
_CHAT_RATE_MAX = 10   # requests per window
_CHAT_RATE_WINDOW = 60  # seconds


# Curated set of model ids the user is most likely to want to swap to
# without typing the full provider path. Anything else is allowed too —
# this is just a discoverability list shown by bare `/use`.
_MODEL_HINTS = [
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "moonshotai/kimi-k2.6:free",
    "qwen/qwen3-coder:free",
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


def _check_chat_rate(request: Request) -> None:
    ip = (request.client.host if request.client else None) or "unknown"
    now = time.monotonic()
    timestamps = _chat_rate[ip]
    # Drop timestamps outside the sliding window
    _chat_rate[ip] = [t for t in timestamps if now - t < _CHAT_RATE_WINDOW]
    if len(_chat_rate[ip]) >= _CHAT_RATE_MAX:
        raise HTTPException(429, "rate limit exceeded — max 10 messages/minute")
    _chat_rate[ip].append(now)


@app.post("/api/chat/send", dependencies=[Depends(require_web_auth)])
async def chat_send(request: Request):
    """SSE stream of the agent's reply to a posted user message.

    Accepts optional `stream_id` in the body — if provided, the client
    can call /api/chat/cancel with that ID to interrupt this stream
    mid-flight. The cancellation takes effect at the next chunk
    boundary (we can't interrupt a tool call already in progress)."""
    _check_chat_rate(request)
    payload = await request.json()
    user_message = (payload or {}).get("message", "").strip()
    if not user_message:
        raise HTTPException(400, "missing 'message'")

    stream_id = (payload or {}).get("stream_id") or secrets.token_urlsafe(12)
    agent = _get_chat_agent()

    # /use <model> — swap the primary model for this chat session without
    # restart. Empty arg shows current model + known catalogue. "reset"
    # restores the default. Fallbacks still kick in if the new primary
    # 429s, so a typo never bricks the chat.
    if user_message.startswith("/use"):
        reply = _handle_use_command(agent, user_message[4:].strip())

        def cmd_gen():
            yield _format_sse_data(reply)
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
        _chat_agent_lock.acquire()
        reply_buf: list[str] = []
        user_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            _drain_notifications_for_chat(agent)
            for chunk in agent.chat_stream(user_message, source="web"):
                if stream_id in _cancelled_streams:
                    cancelled = True
                    break
                reply_buf.append(chunk)
                yield _format_sse_data(chunk)
        except Exception as e:
            msg = str(e)
            if "All providers exhausted" in msg or "token_quota_exceeded" in msg or "Tokens per minute" in msg:
                err_chunk = "[All AI providers are currently rate-limited. Wait a moment and try again.]"
            elif "All providers exhausted" in type(e).__name__ or "RuntimeError" in type(e).__name__ and "provider" in msg.lower():
                err_chunk = "[All AI providers are currently unavailable. Try again shortly.]"
            else:
                err_chunk = f"[error: {type(e).__name__}: {e}]"
            reply_buf.append(err_chunk)
            yield _format_sse_data(err_chunk)
        finally:
            _active_streams.discard(stream_id)
            _cancelled_streams.discard(stream_id)
            if cancelled:
                cancel_marker = "\n\n[stopped by user]"
                reply_buf.append(cancel_marker)
                yield _format_sse_data(cancel_marker)
            if _chat_memory is not None:
                _chat_memory.save_session(agent.history)
                # No longer writes _chat_log.jsonl — the Agent
                # journaled both turns into _transcript.jsonl as they
                # happened (PR #111), and /api/chat/history now reads
                # from there.
            _chat_agent_lock.release()
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


@app.post("/api/chat/cancel", dependencies=[Depends(require_web_auth)])
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


# --- SSE: live event stream ----------------------------------------------

@app.get("/events", dependencies=[Depends(require_web_auth)])
async def events_endpoint():
    return StreamingResponse(
        _tail_events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _tail_events():
    """Stream new lines from _events.jsonl over SSE.

    Re-opens the file on every poll cycle. A long-lived handle keeps a
    Python read buffer that misses appends made by other processes
    (heartbeat/telegram/repl) writing into the same shared file.
    """
    EVENTS_PATH.touch(exist_ok=True)

    # Initial tail: scan back from EOF until we've collected at least
    # INITIAL_TAIL_MIN_REAL non-infra events (so the browser always sees
    # actual agent activity, not just service_pings). Capped at
    # INITIAL_TAIL_MAX lines to bound memory on heavy infra-only periods.
    with EVENTS_PATH.open("rb") as f:
        all_lines = f.readlines()
        pos = f.tell()
    take = 0
    real = 0
    for raw in reversed(all_lines):
        take += 1
        try:
            ev = json.loads(raw.decode("utf-8", errors="replace")).get("event")
            if ev not in SSE_INFRA_EVENTS:
                real += 1
        except Exception:
            pass
        if real >= INITIAL_TAIL_MIN_REAL or take >= INITIAL_TAIL_MAX:
            break
    for raw in all_lines[-take:]:
        yield _format_sse(raw.decode("utf-8", errors="replace"))

    while True:
        try:
            size = EVENTS_PATH.stat().st_size
            if size < pos:
                # File was truncated/rotated; resume from new EOF.
                pos = 0
            if size > pos:
                with EVENTS_PATH.open("rb") as f:
                    f.seek(pos)
                    chunk = f.read()
                    pos += len(chunk)
                for raw in chunk.splitlines(keepends=True):
                    if raw.endswith(b"\n"):
                        yield _format_sse(raw.decode("utf-8", errors="replace"))
        except FileNotFoundError:
            pass
        await asyncio.sleep(POLL_INTERVAL)


def _format_sse(json_line: str) -> str:
    payload = json_line.rstrip("\n")
    return f"data: {payload}\n\n" if payload else ""


def _format_sse_data(data: str) -> str:
    """Encode arbitrary text as JSON in one SSE data line.

    Raw SSE data lines are line-oriented. JSON preserves exact newlines,
    spaces, and code blocks, and keeps the browser parser simple.
    """
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# --- Filesystem helpers --------------------------------------------------

def _list_memory_entries() -> list[dict]:
    if not MEMORY_DIR.exists():
        return []
    provenance = _memory_write_provenance()
    entries: list[dict] = []
    for path in MEMORY_DIR.glob("*.md"):
        if path.name in {"MEMORY.md", "README.md"}:
            continue
        meta = _parse_frontmatter(path)
        source = provenance.get(path.name, {})
        entries.append({
            "filename": path.name,
            "name": meta.get("name", path.stem),
            "description": meta.get("description", ""),
            "type": meta.get("type", "unknown"),
            "mtime": path.stat().st_mtime,
            "source_service": source.get("service"),
            "source_event": source.get("event"),
            "source_action": source.get("action"),
            "source_ts": source.get("ts"),
        })
    entries.sort(key=lambda e: e["mtime"], reverse=True)
    return entries


def _memory_write_provenance() -> dict[str, dict]:
    """Return the most recent memory_write event keyed by memory filename."""
    out: dict[str, dict] = {}
    if not EVENTS_PATH.exists():
        return out
    try:
        with EVENTS_PATH.open("r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-5000:]
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("event") != "memory_write":
            continue
        name = rec.get("name")
        if not name:
            continue
        out[str(name)] = {
            "ts": rec.get("ts"),
            "service": rec.get("service"),
            "event": rec.get("event"),
            "action": rec.get("action"),
        }
    return out


def _parse_frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}
    block = text[4:end]
    meta: dict[str, str] = {}
    for line in block.splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        meta[k.strip()] = v.strip()
    return meta


def _list_log_files() -> list[dict]:
    logs_root = MEMORY_DIR / "logs"
    if not logs_root.exists():
        return []
    out: list[dict] = []
    for path in logs_root.rglob("*.md"):
        out.append({
            "rel": path.relative_to(logs_root).as_posix(),
            "date": path.stem,
            "size_kb": round(path.stat().st_size / 1024, 1),
            "mtime": path.stat().st_mtime,
        })
    out.sort(key=lambda e: e["mtime"], reverse=True)
    return out


def _safe_subpath(rel: str, root: Path) -> Path | None:
    """Reject path traversal; resolve a user-supplied subpath under root."""
    try:
        candidate = (root / rel).resolve()
        root_resolved = root.resolve()
    except (OSError, ValueError):
        return None
    try:
        candidate.relative_to(root_resolved)
    except ValueError:
        return None
    return candidate


# --- Static SPA hosting (mounted last so /api/* takes precedence) --------

# In production the Docker image copies the built SPA into SPA_DIST_DIR.
# If the directory is missing (e.g. running uvicorn directly during
# development without a build), we skip the mount so /api/* still works
# and the developer is expected to use `npm run dev` for the UI.
if SPA_DIST_DIR.exists():
    app.mount("/assets", StaticFiles(directory=SPA_DIST_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa_catchall(full_path: str):
        """Serve the SPA index for any non-API path so client-side
        routing works on hard refresh."""
        return FileResponse(SPA_DIST_DIR / "index.html")
