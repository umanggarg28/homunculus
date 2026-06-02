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
        _chat_memory.queue_notification(f"[{source}] {message}")
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
    explicit_tick = mem.peek_next_tick()

    # When no explicit tick is scheduled, estimate from last heartbeat event + default interval.
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
            current = {
                "id": f"turn-{len(turns) + 1}",
                "started_at": ts,
                "service": service,
                "user": rec.get("text", ""),
                "assistant": "",
                "models": [],
                "tools": [],
                "guards": [],
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_tokens": 0,
                "cost_cents": 0.0,
            }
            continue

        if current is None:
            continue

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

        if event in {"output_guard", "self_correction", "budget_blocked", "provider_cooled"}:
            current["guards"].append({
                "ts": ts,
                "event": event,
                "name": rec.get("name", ""),
                "text": rec.get("text", ""),
                "result": rec.get("result", ""),
            })

    if current is not None:
        turns.append(current)

    for turn in turns:
        turn["cost_cents"] = round(float(turn.get("cost_cents") or 0.0), 4)
    return turns[-limit:][::-1]


@app.get("/api/stats/today", dependencies=[Depends(require_web_auth)])
def stats_today() -> JSONResponse:
    """Return activity counts since UTC midnight today.

    Uses server-side UTC midnight so the window is consistent regardless
    of the client's timezone.
    """
    from datetime import timezone
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

    Saved sessions can contain interrupted turns: user message, assistant
    tool_call message, tool results, then no final assistant text because the
    stream was cancelled or the process stopped mid-turn. Rendering those user
    messages alone makes the chat page look like the agent never replied. For
    the UI transcript, keep only visible user→assistant pairs.
    """
    memory = _chat_memory or Memory(MEMORY_DIR)
    return JSONResponse(_visible_chat_history(memory.load_session()))


def _visible_chat_history(history: list[dict]) -> list[dict]:
    """Filter persisted agent history down to visible complete chat turns."""
    messages = []
    pending_user: dict | None = None
    for idx, msg in enumerate(history):
        role = msg.get("role")
        content = msg.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        # Skip heartbeat notifications — they live in LLM context for
        # follow-up questions but shouldn't appear as chat bubbles.
        if content.startswith("[notification I sent you at"):
            continue
        if role == "user":
            pending_user = {
                "id": f"persisted-{idx}",
                "role": role,
                "content": content,
            }
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
            messages.append({
                "id": f"persisted-{idx}",
                "role": role,
                "content": content,
            })
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
        fresh = agent.memory.drain_pending_notifications()
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

    # Drain any notifications fired by heartbeat (or other processes)
    # since the last chat turn, so follow-ups like "explain it" arrive
    # with context. Same mechanism the Telegram bot uses.
    _drain_notifications_for_chat(agent)

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
            msg = str(e)
            if "All providers exhausted" in msg or "token_quota_exceeded" in msg or "Tokens per minute" in msg:
                yield _format_sse_data("[All AI providers are currently rate-limited. Wait a moment and try again.]")
            elif "All providers exhausted" in type(e).__name__ or "RuntimeError" in type(e).__name__ and "provider" in msg.lower():
                yield _format_sse_data("[All AI providers are currently unavailable. Try again shortly.]")
            else:
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
