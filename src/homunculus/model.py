"""One streaming call to the language model."""

import json
from collections.abc import AsyncIterator
from typing import Protocol

import httpx

# A chat message in the OpenAI-compatible format: {"role": ..., "content": ...}.
Message = dict[str, str]


class ModelError(Exception):
    """The model call failed. The message is safe to show to the operator."""


class Model(Protocol):
    def stream(self, messages: list[Message]) -> AsyncIterator[str]:
        """Send the conversation and yield the reply as it arrives, chunk by chunk."""
        ...


URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL_ID = "z-ai/glm-5.3-flash"

# Route only to providers that keep no copy of the request and do not train on it.
# Fixed here rather than in configuration so that it cannot be switched off.
PRIVACY_ROUTING = {"zdr": True, "data_collection": "deny"}


class OpenRouterModel:
    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None) -> None:
        self._api_key = api_key
        self._client = client or httpx.AsyncClient(timeout=120)

    async def stream(self, messages: list[Message]) -> AsyncIterator[str]:
        body = {
            "model": MODEL_ID,
            "messages": messages,
            "stream": True,
            "provider": PRIVACY_ROUTING,
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}
        async with self._client.stream("POST", URL, json=body, headers=headers) as response:
            if response.status_code != 200:
                await response.aread()
                raise ModelError(
                    f"Model request failed ({response.status_code}): {_error_message(response)}"
                )
            # The reply arrives as server-sent events: "data: {json}" lines, ending
            # with "data: [DONE]". Lines starting with ":" are keep-alive comments.
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line.removeprefix("data: ")
                if data == "[DONE]":
                    return
                chunk = json.loads(data)
                if "error" in chunk:
                    raise ModelError(f"Model stopped mid-reply: {chunk['error']['message']}")
                for choice in chunk["choices"]:
                    if text := choice["delta"].get("content"):
                        yield text


def _error_message(response: httpx.Response) -> str:
    try:
        return str(response.json()["error"]["message"])
    except (ValueError, KeyError, TypeError):
        return response.text[:200]
