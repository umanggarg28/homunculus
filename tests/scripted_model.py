"""A stand-in for the real model that replies from a script.

Tests use it so that every run is fast, free and repeatable. Each reply in the
script is a list of text chunks, streamed in order, one reply per model call.
"""

import asyncio
from collections.abc import AsyncIterator

from homunculus.model import Message


class ScriptedModel:
    def __init__(self, replies: list[list[str]]) -> None:
        self._replies = list(replies)
        self.received: list[list[Message]] = []

    async def stream(self, messages: list[Message]) -> AsyncIterator[str]:
        self.received.append(list(messages))
        for chunk in self._replies.pop(0):
            yield chunk


class SlowScriptedModel(ScriptedModel):
    """A scripted model that pauses between chunks, so calls can overlap in time."""

    async def stream(self, messages: list[Message]) -> AsyncIterator[str]:
        async for chunk in super().stream(messages):
            await asyncio.sleep(0.01)
            yield chunk
