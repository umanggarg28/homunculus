"""Task routes — CRUD over the task store plus the ad-hoc run-stream endpoint.

The task store itself (``wa._task_store()``) stays in ``web_api`` because it is
shared with the chat/heartbeat surfaces; these handlers are the HTTP face of it.
"""

from datetime import datetime, UTC

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from homunculus.core import Agent
from homunculus.tasks import ALLOWED_RECURRENCE
from homunculus.transports import web_api as wa

router = APIRouter()


@router.get("/api/tasks", dependencies=[Depends(wa.require_web_auth)])
def tasks_list(status: str = "all") -> JSONResponse:
    """List tasks. status = active | completed | cancelled | all."""
    try:
        items = wa._task_store().list(status=status)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return JSONResponse(items)


@router.post("/api/tasks", dependencies=[Depends(wa.require_web_auth)])
async def tasks_create(request: Request) -> JSONResponse:
    body = await request.json()
    title = (body or {}).get("title", "").strip()
    if not title:
        raise HTTPException(400, "title is required")
    try:
        task = wa._task_store().create(
            title=title,
            description=body.get("description", ""),
            due_at=body.get("due_at"),
            recurrence=body.get("recurrence", "none"),
            notify=body.get("notify", False),
            success_criteria=body.get("success_criteria"),
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return JSONResponse(task)


@router.patch("/api/tasks/{task_id}", dependencies=[Depends(wa.require_web_auth)])
async def tasks_update(task_id: str, request: Request) -> JSONResponse:
    body = await request.json() or {}
    # Linking a task to a skill is a real operation (not a tasks.json hand-edit):
    # this is what binds a skill to the task that runs it, so an approved skill
    # can't sit orphaned and never run. A non-empty skill must exist.
    skill = body.get("skill")
    if skill:
        from homunculus.skills import Skills
        if Skills(wa.MEMORY_DIR).load(skill) is None:
            raise HTTPException(400, f"skill {skill!r} does not exist — create it first")
    try:
        task = wa._task_store().update(
            task_id,
            title=body.get("title"),
            description=body.get("description"),
            due_at=body.get("due_at"),
            recurrence=body.get("recurrence"),
            notify=body.get("notify"),
            success_criteria=body.get("success_criteria"),
            skill=skill,
        )
    except KeyError:
        raise HTTPException(404, f"task '{task_id}' not found") from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return JSONResponse(task)


@router.post("/api/tasks/{task_id}/complete", dependencies=[Depends(wa.require_web_auth)])
async def tasks_complete(task_id: str, request: Request) -> JSONResponse:
    body = (await request.json()) if request.headers.get("content-length") else {}
    try:
        task = wa._task_store().complete(task_id, result=(body or {}).get("result", ""))
    except KeyError:
        raise HTTPException(404, f"task '{task_id}' not found") from None
    return JSONResponse(task)


@router.post("/api/tasks/{task_id}/cancel", dependencies=[Depends(wa.require_web_auth)])
async def tasks_cancel(task_id: str, request: Request) -> JSONResponse:
    body = (await request.json()) if request.headers.get("content-length") else {}
    try:
        task = wa._task_store().cancel(task_id, reason=(body or {}).get("reason", ""))
    except KeyError:
        raise HTTPException(404, f"task '{task_id}' not found") from None
    return JSONResponse(task)


@router.post("/api/tasks/{task_id}/run-now", dependencies=[Depends(wa.require_web_auth)])
def tasks_run_now(task_id: str) -> JSONResponse:
    """Legacy "schedule the task to fire on the next heartbeat tick" endpoint.

    For interactive streamed execution use POST /api/tasks/{task_id}/run-stream
    which actually runs the task right now and streams the events back.
    """
    try:
        task = wa._task_store().run_now(task_id)
    except KeyError:
        raise HTTPException(404, f"task '{task_id}' not found") from None
    return JSONResponse(task)


@router.post("/api/tasks/{task_id}/run-stream", dependencies=[Depends(wa.require_web_auth)])
async def tasks_run_stream(task_id: str, request: Request):
    """Run a single task right now and stream the agent's execution as SSE.

    The agent is fresh (NOT the chat agent — task execution is isolated, like
    a heartbeat tick is). TaskGuard installs the same success_criteria guard
    we use during scheduled runs. The post-execution housekeeping (record
    failure / advance due_at) is identical to heartbeat.tick().

    Pass `?dry_run=1` to exercise the whole path without messaging anyone:
    the loop, guard, criteria and settlement all run for real, only outbound
    delivery is suppressed. The run is marked so it stays out of the eval
    scorecards — a rehearsal must not move the numbers a verdict reads.

    This is T1.4 of docs/CAPABILITY_ROADMAP.md — the "click ARMED → stream
    in place" UX that removes the trip to Traces for ad-hoc task runs.
    """
    wa._check_chat_rate(request)
    dry = str(request.query_params.get("dry_run", "")).lower() in {"1", "true", "yes"}
    store = wa._task_store()
    task = store.get(task_id)
    if task is None:
        raise HTTPException(404, f"task '{task_id}' not found")
    if task.get("status") != "active":
        raise HTTPException(409, f"task is {task.get('status')} — only active tasks can be run")

    # Run-now shares the scheduled tick's execution core so the two can't
    # drift: prepare_task_run does the same planning (playbook injection,
    # capability gate, folded success_criteria, state sequence) + guard, and
    # settle_task_* does the same close-out. The only differences are
    # streaming and forced=True — the task's due_at is its next recurrence, so
    # without that note the model would skip it as "not due".
    from contextlib import ExitStack

    from homunculus import events
    from homunculus.heartbeat import (
        prepare_task_run,
        settle_task_failure,
        settle_task_outcome,
    )
    from homunculus.user_tz import now_user_tz

    # prepare_task_run also stamps last_fired_at, claiming the task before the
    # agent runs so a concurrent heartbeat tick won't pick it up.
    prep = prepare_task_run(
        store, task, wa.MEMORY_DIR,
        now_user_tz().isoformat(timespec="seconds"),
        forced=True,
    )
    if prep is None:
        raise HTTPException(500, f"task '{task_id}' could not be planned (missing skill?)")
    state_sequence, prompt, guard = prep
    # Fresh agent — task execution must NOT share history with the chat
    # session (would pollute future chat turns with task-execution noise).
    # The guard rides THIS agent as run-scoped hooks: a chat turn served by
    # the same process mid-run keeps its own tool calls out of the task's
    # criteria checks and grounding blob.
    fresh_agent = Agent(
        memory=wa._chat_memory,
        pre_execute_hook=guard.on_tool_call,
        post_execute_hook=guard.observe_tool_result,
        pre_turn_hook=guard.on_pre_turn,
        # Withheld in the agent loop, not in the tool: tools run in an MCP
        # stdio subprocess that no in-process flag can reach.
        suppressed_tools={"notify"} if dry else None,
    )
    due_at_before = task.get("due_at")
    started = datetime.now()
    started_iso = started.isoformat(timespec="seconds")
    started_utc = datetime.now(UTC)

    def gen():
        try:
            yield wa._format_sse_data(f"[run-now started at {started_iso}]")
            try:
                # source="heartbeat": a run-now is a task tick, not a chat
                # turn. Tagging it keeps the heartbeat-style prompt (and any
                # mid-run model text) OUT of /api/chat/history — _visible_
                # chat_history drops _NON_CHAT_SOURCES.
                #
                # expected_completions=1: a run-now drives exactly ONE task,
                # so exit the loop the moment it's closed; state_sequence pins
                # a state-machine skill's tool order, same as a scheduled tick.
                #
                # Same task stamp the scheduled tick applies: a run-now emits
                # the same events, and an unattributed one is indistinguishable
                # from another task's when a scorecard windows the log.
                with ExitStack() as stack:
                    stack.enter_context(events.task_context(str(task.get("id") or "")))
                    for chunk in fresh_agent.chat_stream(
                        prompt, source="heartbeat",
                        state_sequence=state_sequence, expected_completions=1,
                    ):
                        yield wa._format_sse_data(chunk)
                    if dry:
                        withheld = fresh_agent.suppressed_calls
                        yield wa._format_sse_data(
                            f"[dry run — {len(withheld)} call(s) withheld, nothing sent]"
                        )
                        for name, call_args in withheld:
                            body = str(call_args.get("text") or call_args)
                            yield wa._format_sse_data(f"[would have sent via {name}]\n{body}")
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
                yield wa._format_sse_data(f"[loop error: {err}]")
                # Same failure recording the scheduled tick uses (infra →
                # partial, real → failure), then end the stream.
                settle_task_failure(
                    wa._chat_memory, store, task, guard, err,
                    due_at_before=due_at_before,
                    duration_s=(datetime.now() - started).total_seconds(),
                    started_utc=started_utc,
                )
                yield "event: done\ndata: end\n\n"
                return

            # Same deterministic close-out as the scheduled tick (success /
            # explicit-close / silent-drop with harness auto-complete). The
            # operator is watching this stream, so suppress the escalation push.
            settle_task_outcome(
                wa._chat_memory, store, task, guard,
                due_at_before=due_at_before,
                started=started,
                started_utc=started_utc,
                fire_escalation_notify=False,
            )
            if dry:
                # After settlement, so it lands on the run the tool layer wrote.
                store.mark_last_run_dry(task_id)
            yield wa._format_sse_data("[run-now finished]")
        finally:
            yield "event: done\ndata: end\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.delete("/api/tasks/{task_id}", dependencies=[Depends(wa.require_web_auth)])
def tasks_delete(task_id: str) -> JSONResponse:
    try:
        wa._task_store().delete(task_id)
    except KeyError:
        raise HTTPException(404, f"task '{task_id}' not found") from None
    return JSONResponse({"ok": True})


@router.get("/api/tasks/meta", dependencies=[Depends(wa.require_web_auth)])
def tasks_meta() -> JSONResponse:
    """Constants the form UI needs (allowed recurrence values, etc.)."""
    return JSONResponse({
        "recurrence_options": sorted(ALLOWED_RECURRENCE),
    })
