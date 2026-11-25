"""HTTP API for the web console and chat channels."""

import asyncio
import dataclasses
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from homunculus import egress, loop
from homunculus.activity import DECISION, Activity, context_of, fail
from homunculus.approvals import Approval, ApprovalError, Approvals, Kind, Status
from homunculus.limits import Budget, Limits
from homunculus.memory import CAPS, Memory, Stale
from homunculus.model import Message, Model, ModelError, Retrying
from homunculus.skills import Skills
from homunculus.tools import Tool
from homunculus.turns import Event, Turn, Turns
from homunculus.waker import Waker, routine_skill, wake_prompt

# How many decided requests the approvals list shows below the pending ones.
RECENT_DECISIONS = 10
# How many jobs the activity screen shows, newest first.
RECENT_JOBS = 100


class ChatRequest(BaseModel):
    message: str


class Decision(BaseModel):
    note: str | None = None


class PageEdit(BaseModel):
    text: str
    # The version the edit was made from, so a save never overwrites a newer change.
    version: str


class Feedback(BaseModel):
    rating: Literal["up", "down"]
    reason: str | None = None


def sse(event: Event) -> str:
    """Format one server-sent event. Its `id` is what a reconnecting page sends back as
    `Last-Event-ID`."""
    return f"id: {event.id}\nevent: {event.name}\ndata: {json.dumps(event.data)}\n\n"


def watch(turn: Turn, after: int = 0) -> StreamingResponse:
    """Stream a turn's events after the one numbered `after`. The page closing ends only
    this stream, never the turn."""

    async def events() -> AsyncIterator[str]:
        async for event in turn.follow(after):
            yield sse(event)

    return StreamingResponse(events(), media_type="text/event-stream")


def create_app(
    model: Model,
    tools: list[Tool],
    activity: Activity,
    approvals: Approvals,
    console: Path | None = None,
    memory: Memory | None = None,
    limits: Limits | None = None,
    budget: Budget | None = None,
    waker: Waker | None = None,
    skills: Skills | None = None,
) -> FastAPI:
    """The HTTP API, and the built web console from `console` when one is given."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # While the server runs, the waker is checked every 30 seconds, or at once when
        # the operator presses Wake now.
        task = asyncio.create_task(_keep_waking(app)) if waker is not None else None
        yield
        if task is not None:
            task.cancel()

    app = FastAPI(title="Homunculus", lifespan=lifespan)
    approvals.mark_interrupted()
    # One operator, one conversation, kept in memory for the life of the process.
    history: list[Message] = []
    # One piece of work at a time: a second message waits until the first reply is complete,
    # so it is sent to the model with the earlier exchange already in the history.
    turn = asyncio.Lock()
    app.state.busy = turn
    # Why the unprompted job running now should give way at its next step, if it should.
    give_way: dict[str, str | None] = {"reason": None}
    # Turns run as tasks of their own, so a page closing does not stop the work.
    turns = Turns()

    @app.post("/api/chat")
    async def chat(request: ChatRequest) -> StreamingResponse:
        """Start a turn for the operator's message, and watch it."""

        async def work(running: Turn) -> None:
            await running.add("turn", {"id": running.id, "message": request.message})
            if waker is not None and waker.working:
                # The operator comes first: the job stops at its next step.
                give_way["reason"] = "operator"
            async with turn:
                try:
                    async for item in loop.run(
                        model,
                        tools,
                        activity,
                        approvals,
                        history,
                        request.message,
                        memory=memory,
                        skills=skills,
                        now=waker.now if waker is not None else None,
                        limits=limits,
                        budget=budget,
                    ):
                        if isinstance(item, loop.ToolStep):
                            await running.add("tool", dataclasses.asdict(item))
                        elif isinstance(item, loop.Stopped):
                            await running.add("notice", {"text": item.message, "limit": item.limit})
                        elif isinstance(item, Retrying):
                            await running.add("retrying", {"attempt": item.attempt, "of": item.of})
                        else:
                            await running.add("delta", {"text": item})
                except ModelError as error:
                    await running.add("error", {"message": str(error)})
                    return
            await running.add("done", {})

        return watch(turns.start(work))

    def find(turn_id: str) -> Turn:
        found = turns.get(turn_id)
        if found is None:
            raise HTTPException(status_code=404, detail=f"there is no turn {turn_id}")
        return found

    @app.get("/api/turns/current", response_model=None)
    async def current_turn() -> dict[str, Any] | Response:
        """The turn in progress, so a page that was closed or reloaded can rejoin it. With
        none running, the answer is 204 No Content: nothing to return, and no error."""
        running = turns.current()
        if running is None:
            return Response(status_code=204)
        return {"id": running.id}

    @app.get("/api/turns/{turn_id}/events")
    async def turn_events(
        turn_id: str, last_event_id: str = Header(default="", alias="Last-Event-ID")
    ) -> StreamingResponse:
        """A turn's events from the start, or after `Last-Event-ID`, then live. The
        standard treats the id as text; one this server did not send starts over."""
        after = int(last_event_id) if last_event_id.isdecimal() else 0
        return watch(find(turn_id), after=after)

    @app.post("/api/turns/{turn_id}/stop", status_code=202)
    async def stop_turn(turn_id: str) -> dict[str, Any]:
        """Stop a running turn. Its stream ends with a `stopped` event."""
        running = find(turn_id)
        if running.done:
            raise HTTPException(status_code=409, detail=f"turn {turn_id} has already ended")
        running.stop()
        return {"id": running.id}

    @app.get("/api/approvals")
    async def list_approvals() -> list[dict[str, Any]]:
        """Pending requests first, then the most recently decided ones, newest first."""
        requests = approvals.all()
        pending = [a for a in requests if a.status == "pending"]
        decided = [a for a in reversed(requests) if a.status != "pending"][:RECENT_DECISIONS]
        return [dataclasses.asdict(a) for a in [*pending, *decided]]

    async def tell_the_agent(approval_id: str, text: str) -> None:
        """Run a turn that tells the agent the outcome of a request, so it can carry on.

        The decision is part of the conversation whatever happens: if the turn fails,
        the notice is added on its own, so the agent reads it in its next turn."""
        async with turn:
            try:
                async for _ in loop.run(
                    model,
                    tools,
                    activity,
                    approvals,
                    history,
                    text,
                    notice=True,
                    memory=memory,
                    skills=skills,
                    now=waker.now if waker is not None else None,
                    limits=limits,
                    budget=budget,
                ):
                    pass
            except Exception as error:
                # The loop has already recorded a model failure in the activity log.
                history.append({"role": "user", "content": text, "notice": True})
                approvals.record_follow_up(approval_id, f"failed: {error}")
                return
        approvals.record_follow_up(approval_id, "replied")

    def record_decision(
        approval_id: str, status: Status, required_kind: Kind | None, note: str | None
    ) -> Approval:
        """Record the operator's decision, refusing an unknown request (404), one that is
        already decided, or a decision that does not suit its kind (409)."""
        try:
            current = approvals.get(approval_id)
        except ApprovalError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        if required_kind is not None and current.kind != required_kind:
            raise HTTPException(
                status_code=409,
                detail=f"request {approval_id} is '{current.kind}', not '{required_kind}'",
            )
        try:
            decided = approvals.decide(approval_id, status, note)
        except ApprovalError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        activity.event(
            DECISION,
            context_of(decided.traceparent),
            {
                "homunculus.approval.id": approval_id,
                "homunculus.approval.status": status,
                "homunculus.approval.note": note,
                "gen_ai.tool.name": decided.tool,
            },
        )
        return decided

    @app.post("/api/approvals/{approval_id}/approve")
    async def approve(approval_id: str, background: BackgroundTasks) -> dict[str, Any]:
        """Run exactly the action that was requested, then tell the agent the result."""
        decided = record_decision(approval_id, "approved", "ask", None)
        by_name = {tool.name: tool for tool in tools}
        # Inside the turn lock, so the action never runs alongside a chat turn's own tools,
        # and in a worker thread, so a slow tool does not stall other requests.
        async with turn:
            # The run joins the trace of the job that asked for it, so it is shown there.
            span = activity.start_tool(
                decided.tool,
                decided.arguments,
                context_of(decided.traceparent),
                call_id=decided.tool_call_id,
            )
            try:
                done = await asyncio.to_thread(
                    loop.run_tool, decided.tool, decided.arguments, by_name
                )
                result = loop.as_text(done)
                decided = approvals.record_result(approval_id, result)
            except BaseException as error:
                fail(span, error, str(error) or type(error).__name__)
                raise
            # The gate's answer was "ask"; the outcome records what the run itself did.
            activity.end_tool(span, "ask", result, loop.outcome_of(done), approval_id)
        background.add_task(
            tell_the_agent, approval_id, f"Approved: {_request_summary(decided)}. Result: {result}"
        )
        return dataclasses.asdict(decided)

    @app.post("/api/approvals/{approval_id}/deny")
    async def deny(
        approval_id: str, background: BackgroundTasks, decision: Decision | None = None
    ) -> dict[str, Any]:
        """Decline a request. Nothing runs; the agent is told, with the reason if given."""
        note = decision.note if decision else None
        decided = record_decision(approval_id, "denied", None, note)
        text = f"Declined: {_request_summary(decided)}{_appended('Reason', note)}"
        background.add_task(tell_the_agent, approval_id, text)
        return dataclasses.asdict(decided)

    @app.post("/api/approvals/{approval_id}/done")
    async def done(
        approval_id: str, background: BackgroundTasks, decision: Decision | None = None
    ) -> dict[str, Any]:
        """Mark a hand-off as done by the operator, and tell the agent."""
        note = decision.note if decision else None
        decided = record_decision(approval_id, "done", "hand off", note)
        text = f"Done by the operator: {_request_summary(decided)}{_appended('Note', note)}"
        background.add_task(tell_the_agent, approval_id, text)
        return dataclasses.asdict(decided)

    @app.get("/api/activity")
    async def list_activity() -> list[dict[str, Any]]:
        """What the agent did, one job per trace, newest first."""
        return activity.jobs()[:RECENT_JOBS]

    @app.post("/api/activity/{job}/feedback")
    async def give_feedback(job: str, feedback: Feedback) -> dict[str, Any]:
        """Record the operator's view of one piece of work, as an evaluation event on it.
        A later rating replaces an earlier one; the file keeps both, since it is only ever
        added to."""
        found = next((j for j in activity.jobs() if j["job"] == job), None)
        if found is None:
            raise HTTPException(status_code=404, detail=f"there is no job {job}")
        if found["span"] is None:
            raise HTTPException(status_code=409, detail=f"job {job} has nothing to rate yet")
        found["feedback"] = activity.rate(
            found["job"], found["span"], feedback.rating, feedback.reason
        )
        return found

    @app.get("/api/history")
    async def conversation() -> list[dict[str, Any]]:
        """The conversation so far, as the operator saw it: their messages, notices of
        their decisions, and the replies."""
        shown = []
        for message in history:
            if message["role"] in ("user", "assistant") and message["content"]:
                entry: dict[str, Any] = {"role": message["role"], "content": message["content"]}
                if message.get("notice"):
                    entry["notice"] = True
                shown.append(entry)
        return shown

    @app.get("/api/budget", response_model=None)
    async def month() -> dict[str, Any] | Response:
        """This month's spend against the budget, for the console. With no budget kept,
        204 No Content: nothing is wrong, so a browser has no error to log."""
        if budget is None:
            return Response(status_code=204)
        return {
            "spent": await budget.spent(),
            "budget": budget.limits.monthly_budget_usd,
            "unprompted_stop_at": budget.limits.unprompted_stop_at,
        }

    @app.get("/api/waker", response_model=None)
    async def waker_status() -> dict[str, Any] | Response:
        """When the agent works on its own. With no waker, 204 No Content: nothing is
        wrong, so a browser has no error to log."""
        if waker is None:
            return Response(status_code=204)
        return waker.status()

    if waker is not None:
        app.state.wake_signal = asyncio.Event()

        async def waker_step() -> None:
            """Wake the agent if it is time. A wake-up due while the operator is busy is
            skipped if it came from the timer; the agent's own wake-ups and Wake now wait
            until the operator is done."""
            assert waker is not None
            reason = waker.peek()
            if reason is None:
                return
            if turn.locked():
                if reason == "the hourly timer":
                    waker.skip_busy()
                return
            async with turn:
                reason = waker.take()
                if reason is None:
                    return
                waker.working = True
                give_way["reason"] = None
                try:
                    # A fresh conversation: unprompted work rests only on what is written
                    # down, and never carries the operator's chat into it.
                    async for _ in loop.run(
                        model,
                        tools,
                        activity,
                        approvals,
                        [],
                        wake_prompt(waker.now(), reason, _routine_instructions(skills, reason)),
                        memory=memory,
                        skills=skills,
                        now=waker.now if waker is not None else None,
                        limits=limits,
                        budget=budget,
                        unprompted=True,
                        interruption=lambda: give_way["reason"],
                        wake_reason=reason,
                        own_files=waker.own_files,
                    ):
                        pass
                except ModelError:
                    pass  # The loop has recorded the failure on the job.
                finally:
                    waker.working = False
                    give_way["reason"] = None

        app.state.waker_step = waker_step

        @app.post("/api/waker/pause")
        async def pause() -> dict[str, Any]:
            assert waker is not None
            waker.pause()
            if waker.working:
                give_way["reason"] = "paused"
            return waker.status()

        @app.post("/api/waker/resume")
        async def resume() -> dict[str, Any]:
            assert waker is not None
            waker.resume()
            return waker.status()

        @app.post("/api/waker/wake", status_code=202)
        async def wake_now() -> dict[str, Any]:
            assert waker is not None
            waker.wake_now()
            app.state.wake_signal.set()
            return waker.status()

    if memory is not None:
        _memory_routes(app, memory)

    if console is not None:
        if (console / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=console / "assets"), name="assets")

        # Every other path is a screen of the single-page console, which picks the
        # screen from the address itself, so each one is answered with index.html.
        @app.get("/{path:path}", include_in_schema=False)
        async def screen(path: str) -> FileResponse:
            if path == "api" or path.startswith("api/"):
                raise HTTPException(status_code=404)
            file = (console / path).resolve()
            if path and file.is_file() and file.is_relative_to(console.resolve()):
                return FileResponse(file)
            return FileResponse(console / "index.html")

    return app


def _memory_routes(app: FastAPI, memory: Memory) -> None:
    """What the console reads and edits in memory. Plain functions, so FastAPI runs them
    in worker threads and one waiting on the memory's lock never holds up the server."""

    @app.get("/api/memory/pages")
    def list_pages() -> list[dict[str, Any]]:
        return memory.pages()

    @app.get("/api/memory/pages/{path:path}")
    def read_page(path: str) -> dict[str, str]:
        try:
            return memory.page(path)
        except LookupError:
            raise HTTPException(status_code=404, detail=f"There is no page {path}.") from None

    @app.put("/api/memory/pages/{path:path}")
    def save_page(path: str, edit: PageEdit) -> dict[str, str]:
        try:
            return memory.save(path, edit.text, edit.version)
        except LookupError:
            raise HTTPException(status_code=404, detail=f"There is no page {path}.") from None
        except Stale as stale:
            raise HTTPException(status_code=409, detail=str(stale)) from None
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None

    @app.get("/api/memory/about")
    def about() -> dict[str, Any]:
        profile = memory.page("profile.md")
        return {
            "sources": memory.sources(),
            "profile": {**profile, "cap": CAPS["profile.md"]},
            "egress": [route._asdict() for route in egress.ROUTES],
        }

    @app.get("/api/memory/sources/{path:path}")
    def read_source(path: str) -> dict[str, str]:
        try:
            return memory.source(path)
        except LookupError:
            raise HTTPException(status_code=404, detail=f"There is no source {path}.") from None

    @app.get("/api/memory/log")
    def log() -> list[dict[str, str]]:
        return memory.log()


def _routine_instructions(skills: Skills | None, reason: str) -> str | None:
    """The instructions of the skill a routine runs, read when it runs, so the routine
    hands its skill to the agent directly. An empty string when the skill is missing."""
    name = routine_skill(reason)
    if name is None:
        return None
    found = skills.instructions(name) if skills is not None else None
    return found or ""


async def _keep_waking(app: FastAPI) -> None:
    while True:
        try:
            await app.state.waker_step()
        except Exception:
            pass  # A failed wake-up is recorded on its job; the waker keeps going.
        try:
            await asyncio.wait_for(app.state.wake_signal.wait(), timeout=30)
        except TimeoutError:
            pass
        app.state.wake_signal.clear()


def _request_summary(approval: Approval) -> str:
    """The request as the agent is told it: its id, the tool and the exact arguments."""
    return f"request {approval.id}, {approval.tool} {approval.arguments}"


def _appended(label: str, text: str | None) -> str:
    return f". {label}: {text}" if text else "."
