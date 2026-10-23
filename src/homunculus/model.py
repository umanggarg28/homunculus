"""One streaming call to the language model."""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

# A chat message in the Chat Completions format: {"role": ..., "content": ...}, plus
# "tool_calls" on an assistant message and "tool_call_id" on a tool result.
Message = dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    """The model asking to use a tool. `arguments` is JSON text, exactly as the model wrote it."""

    id: str
    name: str
    arguments: str


class ModelError(Exception):
    """The model call failed. The message is safe to show to the operator."""


class Model(Protocol):
    def stream(
        self, messages: list[Message], tools: list[dict[str, Any]]
    ) -> AsyncIterator[str | ToolCall]:
        """Send the conversation and the tools on offer.

        Yields the reply text as it arrives, chunk by chunk, and each complete tool call.
        """
        ...


OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL_ID = "z-ai/glm-5.3-flash"

# Route only to providers that keep no copy of the request and do not train on it.
# Fixed here rather than in configuration so that it cannot be switched off.
PRIVACY_ROUTING = {"zdr": True, "data_collection": "deny"}


class OpenRouterModel:
    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None) -> None:
        self._api_key = api_key
        self._client = client or httpx.AsyncClient(timeout=120)

    async def stream(
        self, messages: list[Message], tools: list[dict[str, Any]]
    ) -> AsyncIterator[str | ToolCall]:
        body: dict[str, Any] = {
            "model": MODEL_ID,
            "messages": [_api_fields(message) for message in messages],
            "stream": True,
            "provider": PRIVACY_ROUTING,
        }
        if tools:
            body["tools"] = tools
        headers = {"Authorization": f"Bearer {self._api_key}"}
        async with self._client.stream(
            "POST", OPENROUTER_CHAT_URL, json=body, headers=headers
        ) as response:
            if response.status_code != 200:
                await response.aread()
                raise ModelError(
                    f"Model request failed ({response.status_code}): {_error_message(response)}"
                )
            # A tool call arrives in fragments spread over several chunks, keyed by its
            # index: the id and name first, then the arguments a few characters at a time.
            # They are joined here and handed over whole once the stream ends.
            calls: dict[int, dict[str, str]] = {}
            # The reply arrives as server-sent events: "data: {json}" lines, ending
            # with "data: [DONE]". Lines starting with ":" are keep-alive comments.
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line.removeprefix("data: ")
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                if "error" in chunk:
                    raise ModelError(f"Model stopped mid-reply: {chunk['error']['message']}")
                for choice in chunk["choices"]:
                    delta = choice["delta"]
                    if text := delta.get("content"):
                        yield text
                    for fragment in delta.get("tool_calls") or []:
                        call = calls.setdefault(
                            fragment["index"], {"id": "", "name": "", "arguments": ""}
                        )
                        call["id"] = fragment.get("id") or call["id"]
                        function = fragment.get("function") or {}
                        call["name"] = function.get("name") or call["name"]
                        call["arguments"] += function.get("arguments") or ""
            for _, call in sorted(calls.items()):
                yield ToolCall(id=call["id"], name=call["name"], arguments=call["arguments"])


def _error_message(response: httpx.Response) -> str:
    try:
        return str(response.json()["error"]["message"])
    except (ValueError, KeyError, TypeError):
        return response.text[:200]


# The fields the Chat Completions format defines for a message. Anything else a
# message carries is for this program's own use and is not sent.
API_FIELDS = ("role", "content", "tool_calls", "tool_call_id")


def _api_fields(message: Message) -> Message:
    return {key: value for key, value in message.items() if key in API_FIELDS}
