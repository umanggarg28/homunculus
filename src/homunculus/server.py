"""HTTP API for the web console and chat channels."""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from homunculus import loop
from homunculus.model import Message, Model, ModelError


class ChatRequest(BaseModel):
    message: str


def sse(event: str, data: dict[str, Any]) -> str:
    """Format one server-sent event."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def create_app(model: Model) -> FastAPI:
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
                    async for chunk in loop.run(model, history, request.message):
                        yield sse("delta", {"text": chunk})
                except ModelError as error:
                    yield sse("error", {"message": str(error)})
                    return
                yield sse("done", {})

        return StreamingResponse(events(), media_type="text/event-stream")

    return app
