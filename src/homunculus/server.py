"""HTTP API for the web console and chat channels."""

import asyncio
import dataclasses
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from homunculus import loop
from homunculus.activity import DECISION, Activity, context_of, fail
from homunculus.approvals import Approval, ApprovalError, Approvals, Kind, Status
from homunculus.model import Message, Model, ModelError
from homunculus.tools import Tool

# How many decided requests the approvals list shows below the pending ones.
RECENT_DECISIONS = 10
# How many jobs the activity screen shows, newest first.
RECENT_JOBS = 100


class ChatRequest(BaseModel):
    message: str


class Decision(BaseModel):
    note: str | None = None


class Feedback(BaseModel):
    rating: Literal["up", "down"]
    reason: str | None = None


def sse(event: str, data: dict[str, Any]) -> str:
    """Format one server-sent event."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def create_app(
    model: Model,
    tools: list[Tool],
    activity: Activity,
    approvals: Approvals,
    console: Path | None = None,
) -> FastAPI:
    """The HTTP API, and the built web console from `console` when one is given."""
    app = FastAPI(title="Homunculus")
    approvals.mark_interrupted()
    # One operator, one conversation, kept in memory for the life of the process.
    history: list[Message] = []
    # One piece of work at a time: a second message waits until the first reply is complete,
    # so it is sent to the model with the earlier exchange already in the history.
    turn = asyncio.Lock()

    @app.post("/api/chat")
    async def chat(request: ChatRequest) -> StreamingResponse:
        async def events() -> AsyncIterator[str]:
            async with turn:
                try:
                    async for item in loop.run(
                        model, tools, activity, approvals, history, request.message
                    ):
                        if isinstance(item, loop.ToolStep):
                            yield sse("tool", dataclasses.asdict(item))
                        else:
                            yield sse("delta", {"text": item})
                except ModelError as error:
                    yield sse("error", {"message": str(error)})
                    return
                yield sse("done", {})

        return StreamingResponse(events(), media_type="text/event-stream")

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
                    model, tools, activity, approvals, history, text, notice=True
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


def _request_summary(approval: Approval) -> str:
    """The request as the agent is told it: its id, the tool and the exact arguments."""
    return f"request {approval.id}, {approval.tool} {approval.arguments}"


def _appended(label: str, text: str | None) -> str:
    return f". {label}: {text}" if text else "."
