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
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles

import tools
from core import Agent
from memory import Memory
from tasks import ALLOWED_RECURRENCE, TaskStore


# --- Config ---------------------------------------------------------------

EVENTS_PATH = Path(os.environ.get("HOMUNCULUS_EVENTS_PATH", "_events.jsonl"))
MEMORY_DIR = Path(os.environ.get("HOMUNCULUS_MEMORY_DIR", "./memory"))
TASKS_DIR = Path(os.environ.get("HOMUNCULUS_TASKS_DIR", "./tasks"))
SPA_DIST_DIR = Path(os.environ.get("HOMUNCULUS_WEB_DIST", "/app/web-dist"))
WEB_AUTH_TOKEN = os.environ.get("HOMUNCULUS_WEB_AUTH_TOKEN", "").strip()

POLL_INTERVAL = 0.25
INITIAL_TAIL = 50

# Per-service liveness thresholds for /api/status.
STATUS_IDLE_SECONDS = 12 * 60
STATUS_STALE_SECONDS = 60 * 60


app = FastAPI(title="Homunculus API")

# Lazy-init agent + memory for the chat endpoint. Memory() opens lazily
# so the service starts cleanly even before any conversation exists.
_chat_memory: Memory | None = None
_chat_agent: Agent | None = None


def _get_chat_agent() -> Agent:
    global _chat_agent, _chat_memory
    if _chat_agent is None:
        _chat_memory = Memory(MEMORY_DIR)
        # The web service has no interactive stdin. If shell_exec prompted
        # here, the request would hang forever, so treat browser chat like
        # Telegram/heartbeat until an explicit approval UI exists.
        tools.init(_chat_memory, autonomous=True)
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


@app.get("/api/status", dependencies=[Depends(require_web_auth)])
def status() -> JSONResponse:
    """Per-service liveness inferred from event-stream freshness."""
    services = ["repl", "heartbeat", "telegram", "feed"]
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
    if _chat_agent is not None:
        _chat_agent.history = []

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
                    if ts and (entry["last_used"] is None or ts > entry["last_used"]):
                        entry["last_used"] = ts
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
    try:
        task = _task_store().run_now(task_id)
    except KeyError:
        raise HTTPException(404, f"task '{task_id}' not found")
    return JSONResponse(task)


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
    """Return persisted user/assistant chat turns for UI hydration."""
    memory = _chat_memory or Memory(MEMORY_DIR)
    messages = []
    for idx, msg in enumerate(memory.load_session()):
        role = msg.get("role")
        content = msg.get("content")
        if role in {"user", "assistant"} and content:
            messages.append({
                "id": f"persisted-{idx}",
                "role": role,
                "content": content,
            })
    return JSONResponse(messages)


# Stream cancellation state.
#   - _active_streams: currently-running stream IDs (so cancel can validate)
#   - _cancelled_streams: stream IDs whose owner asked us to stop
# A stream's generator checks `_cancelled_streams` between yielded chunks
# and exits cleanly with a [stopped] marker if its ID is in the set.
_active_streams: set[str] = set()
_cancelled_streams: set[str] = set()


@app.post("/api/chat/send", dependencies=[Depends(require_web_auth)])
async def chat_send(request: Request):
    """SSE stream of the agent's reply to a posted user message.

    Accepts optional `stream_id` in the body — if provided, the client
    can call /api/chat/cancel with that ID to interrupt this stream
    mid-flight. The cancellation takes effect at the next chunk
    boundary (we can't interrupt a tool call already in progress)."""
    payload = await request.json()
    user_message = (payload or {}).get("message", "").strip()
    if not user_message:
        raise HTTPException(400, "missing 'message'")

    stream_id = (payload or {}).get("stream_id") or secrets.token_urlsafe(12)
    agent = _get_chat_agent()

    def gen():
        _active_streams.add(stream_id)
        cancelled = False
        try:
            for chunk in agent.chat_stream(user_message):
                if stream_id in _cancelled_streams:
                    cancelled = True
                    break
                yield _format_sse_data(chunk)
        except Exception as e:
            yield _format_sse_data(f"[error: {type(e).__name__}: {e}]")
        finally:
            _active_streams.discard(stream_id)
            _cancelled_streams.discard(stream_id)
            if cancelled:
                yield _format_sse_data("\n\n[stopped by user]")
            if _chat_memory is not None:
                _chat_memory.save_session(agent.history)
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

    # Initial tail: send the last INITIAL_TAIL lines and remember the
    # byte offset so we can resume from there.
    with EVENTS_PATH.open("rb") as f:
        all_lines = f.readlines()
        for raw in all_lines[-INITIAL_TAIL:]:
            yield _format_sse(raw.decode("utf-8", errors="replace"))
        pos = f.tell()

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
    entries: list[dict] = []
    for path in MEMORY_DIR.glob("*.md"):
        if path.name in {"MEMORY.md", "README.md"}:
            continue
        meta = _parse_frontmatter(path)
        entries.append({
            "filename": path.name,
            "name": meta.get("name", path.stem),
            "description": meta.get("description", ""),
            "type": meta.get("type", "unknown"),
            "mtime": path.stat().st_mtime,
        })
    entries.sort(key=lambda e: e["mtime"], reverse=True)
    return entries


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
