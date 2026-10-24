"""A stand-in for the real model that replies from a script.

Tests use it so that every run is fast, free and repeatable. Each reply in the
script is a list of items streamed in order, one reply per model call: a str is
a piece of text, a ToolCall is a request to use a tool.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from homunculus.model import Message, ToolCall, Usage

Reply = list[str | ToolCall | Usage]


class ScriptedModel:
    name = "scripted"
    provider = "scripted"

    def __init__(self, replies: list[Reply]) -> None:
        self._replies = list(replies)
        self.received: list[list[Message]] = []
        self.offered_tools: list[list[dict[str, Any]]] = []

    async def stream(
        self, messages: list[Message], tools: list[dict[str, Any]]
    ) -> AsyncIterator[str | ToolCall | Usage]:
        self.received.append(list(messages))
        self.offered_tools.append(tools)
        for item in self._replies.pop(0):
            yield item


class SlowScriptedModel(ScriptedModel):
    """A scripted model that pauses between items, so calls can overlap in time."""

    async def stream(
        self, messages: list[Message], tools: list[dict[str, Any]]
    ) -> AsyncIterator[str | ToolCall | Usage]:
        async for item in super().stream(messages, tools):
            await asyncio.sleep(0.01)
            yield item
