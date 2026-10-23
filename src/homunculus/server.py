"""HTTP API for the web console and chat channels."""

import asyncio
import dataclasses
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from homunculus import loop
from homunculus.activity import ActivityLog
from homunculus.approvals import Approval, ApprovalError, Approvals
from homunculus.model import Message, Model, ModelError
from homunculus.tools import Tool

# How many decided requests the approvals list shows below the pending ones.
RECENT_DECISIONS = 10


class ChatRequest(BaseModel):
    message: str


class Decision(BaseModel):
    note: str | None = None


def sse(event: str, data: dict[str, Any]) -> str:
    """Format one server-sent event."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def create_app(
    model: Model,
    tools: list[Tool],
    activity: ActivityLog,
    approvals: Approvals,
    console: Path | None = None,
) -> FastAPI:
    """The HTTP API, and the built web console from `console` when one is given."""
    app = FastAPI(title="Homunculus")
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

    async def tell_the_agent(text: str) -> None:
        """Run a turn that tells the agent the outcome of a request, so it can carry on."""
        async with turn:
            try:
                async for _ in loop.run(
                    model, tools, activity, approvals, history, text, notice=True
                ):
                    pass
            except ModelError:
                pass  # recorded in the activity log by the loop; the decision itself stands

    def decide(approval_id: str, status: str, kind: str | None, note: str | None) -> Approval:
        try:
            current = approvals.get(approval_id)
        except ApprovalError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        if kind is not None and current.kind != kind:
            raise HTTPException(
                status_code=409, detail=f"request {approval_id} is '{current.kind}', not '{kind}'"
            )
        try:
            decided = approvals.decide(approval_id, status, note)
        except ApprovalError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        activity.record("approval", id=approval_id, tool=decided.tool, status=status, note=note)
        return decided

    @app.post("/api/approvals/{approval_id}/approve")
    async def approve(approval_id: str, background: BackgroundTasks) -> dict[str, Any]:
        """Run exactly the action that was requested, then tell the agent the result."""
        decided = decide(approval_id, "approved", "ask", None)
        by_name = {tool.name: tool for tool in tools}
        result = loop.run_tool(decided.tool, decided.arguments, by_name)
        decided = approvals.record_result(approval_id, result)
        activity.record(
            "tool", name=decided.tool, arguments=decided.arguments, answer="approved", result=result
        )
        background.add_task(tell_the_agent, f"Approved: {_describe(decided)}. Result: {result}")
        return dataclasses.asdict(decided)

    @app.post("/api/approvals/{approval_id}/deny")
    async def deny(
        approval_id: str, background: BackgroundTasks, decision: Decision | None = None
    ) -> dict[str, Any]:
        """Decline a request. Nothing runs; the agent is told, with the reason if given."""
        note = decision.note if decision else None
        decided = decide(approval_id, "denied", None, note)
        reason = f". Reason: {note}" if note else "."
        background.add_task(tell_the_agent, f"Declined: {_describe(decided)}{reason}")
        return dataclasses.asdict(decided)

    @app.post("/api/approvals/{approval_id}/done")
    async def done(
        approval_id: str, background: BackgroundTasks, decision: Decision | None = None
    ) -> dict[str, Any]:
        """Mark a hand-off as done by the operator, and tell the agent."""
        note = decision.note if decision else None
        decided = decide(approval_id, "done", "hand off", note)
        detail = f". Note: {note}" if note else "."
        background.add_task(tell_the_agent, f"Done by the operator: {_describe(decided)}{detail}")
        return dataclasses.asdict(decided)

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


def _describe(approval: Approval) -> str:
    return f"request {approval.id}, {approval.tool} {approval.arguments}"
