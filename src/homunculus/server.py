"""HTTP API for the web console and chat channels."""

import asyncio
import dataclasses
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from homunculus import loop
from homunculus.activity import ActivityLog
from homunculus.model import Message, Model, ModelError
from homunculus.tools import Tool


class ChatRequest(BaseModel):
    message: str


def sse(event: str, data: dict[str, Any]) -> str:
    """Format one server-sent event."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def create_app(
    model: Model, tools: list[Tool], activity: ActivityLog, console: Path | None = None
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
                    async for item in loop.run(model, tools, activity, history, request.message):
                        if isinstance(item, loop.ToolStep):
                            yield sse("tool", dataclasses.asdict(item))
                        else:
                            yield sse("delta", {"text": item})
                except ModelError as error:
                    yield sse("error", {"message": str(error)})
                    return
                yield sse("done", {})

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.get("/api/history")
    async def conversation() -> list[dict[str, str]]:
        """The conversation so far, as the operator saw it: their messages and the replies."""
        return [
            {"role": message["role"], "content": message["content"]}
            for message in history
            if message["role"] in ("user", "assistant") and message["content"]
        ]

    if console is not None:
        app.mount("/assets", StaticFiles(directory=console / "assets"), name="assets")

        # Every other path is a screen of the single-page console, which picks the
        # screen from the address itself, so each one is answered with index.html.
        @app.get("/{path:path}", include_in_schema=False)
        async def screen(path: str) -> FileResponse:
            if path.startswith("api/"):
                raise HTTPException(status_code=404)
            file = (console / path).resolve()
            if path and file.is_file() and file.is_relative_to(console.resolve()):
                return FileResponse(file)
            return FileResponse(console / "index.html")

    return app
