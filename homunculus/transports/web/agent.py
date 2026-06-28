"""Agent routes — runtime controls, the run-replay feed, containment, schedule."""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from homunculus import agent_controls, tools
from homunculus.memory import Memory
from homunculus.tasks import TaskStore
from homunculus.transports import web_api as wa

router = APIRouter()


@router.get("/api/agent/controls", dependencies=[Depends(wa.require_web_auth)])
def agent_controls_get() -> JSONResponse:
    controls = agent_controls.load_controls().to_dict()
    controls["mode"] = tools.get_mode()
    return JSONResponse(controls)


@router.put("/api/agent/controls", dependencies=[Depends(wa.require_web_auth)])
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
    import homunculus.events as _events
    _events.emit(
        "agent_controls_updated",
        name="agent_controls",
        result=json.dumps(controls, sort_keys=True),
    )
    return JSONResponse(controls)


@router.get("/api/agent/replay", dependencies=[Depends(wa.require_web_auth)])
def agent_replay(limit: int = 12) -> JSONResponse:
    return JSONResponse(wa._build_agent_replay(limit=max(1, min(limit, 50))))


@router.get("/api/containment", dependencies=[Depends(wa.require_web_auth)])
def containment_status() -> JSONResponse:
    """Live guardrail states for the Overview containment panel.

    Every field is derived from real configuration or real event data —
    the panel's drama is presentational, never fictional. If a guard is
    off (e.g. dev runs with HOMUNCULUS_ALLOW_PRIVATE_URLS=1), the panel
    must say so.
    """
    controls = agent_controls.load_controls()
    try:
        budget_cents = max(0.0, float(os.environ.get("HOMUNCULUS_DAILY_BUDGET_USD", "0") or "0") * 100)
    except ValueError:
        budget_cents = 0.0

    # Count recent refusals: tool results that start with ERROR and
    # mention a block/refusal. This is the "breach attempts" number —
    # times a guard actually said no.
    blocked_recent = 0
    if wa.EVENTS_PATH.exists():
        try:
            lines = wa.EVENTS_PATH.read_text(encoding="utf-8", errors="replace").splitlines()[-2000:]
            for ln in lines:
                if '"tool_result"' not in ln:
                    continue
                low = ln.lower()
                if "blocked" in low or "refused" in low or "not permitted" in low:
                    blocked_recent += 1
        except OSError:
            pass

    return JSONResponse({
        "docker_proxy": os.environ.get("DOCKER_HOST", "").startswith("tcp://docker-proxy"),
        "ssrf_guard": os.environ.get("HOMUNCULUS_ALLOW_PRIVATE_URLS") != "1",
        "daily_budget_cents": budget_cents,
        "max_steps": controls.max_steps,
        "paused": controls.paused,
        "mode": tools.get_mode(),
        "delivery_gate": True,  # TaskGuard is unconditionally installed on heartbeat ticks
        "blocked_recent": blocked_recent,
    })


@router.get("/api/agent/upcoming", dependencies=[Depends(wa.require_web_auth)])
def agent_upcoming() -> JSONResponse:
    """What the agent is set to do next.

    Returns:
      - next_tick: ISO datetime the heartbeat will fire (one-shot if
        scheduled, otherwise default-interval estimate from last tick),
      - default_interval_min: the heartbeat's fallback cadence,
      - next_task: earliest-due active task (id, title, due_at), if any.
    """
    mem = wa._chat_memory or Memory(wa.MEMORY_DIR)
    interval_min = int(os.environ.get("HEARTBEAT_INTERVAL_MINUTES", "60"))
    explicit_tick = mem.next_tick.peek()

    # When no explicit tick is scheduled, fall back to estimate from last heartbeat event + interval.
    # The heartbeat now also writes its wake time to memory/_next_tick.txt while sleeping, so
    # explicit_tick covers both agent-scheduled and heartbeat-scheduled wakes.
    estimated_tick: str | None = explicit_tick
    if not estimated_tick and wa.EVENTS_PATH.exists():
        last_hb_ts: float | None = None
        try:
            with wa.EVENTS_PATH.open("r", encoding="utf-8", errors="replace") as f:
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
