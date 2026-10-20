import asyncio
import json
from collections.abc import Callable
from typing import Any

import httpx
from fastapi.testclient import TestClient
from scripted_model import ScriptedModel, SlowScriptedModel

from homunculus.model import Model, OpenRouterModel
from homunculus.server import create_app


def parse_events(body: str) -> list[tuple[str, dict[str, Any]]]:
    """Split a server-sent event stream into (name, data) pairs."""
    events = []
    for block in body.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((fields["event"], json.loads(fields["data"])))
    return events


def chat(client: TestClient, text: str) -> list[tuple[str, dict[str, Any]]]:
    """Send one chat message and return the streamed events."""
    response = client.post("/api/chat", json={"message": text})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    return parse_events(response.text)


def server(model: Model) -> TestClient:
    return TestClient(create_app(model))


def openrouter_model(network: Callable[[httpx.Request], httpx.Response]) -> OpenRouterModel:
    """The real model module, with only the network replaced by `network`."""
    return OpenRouterModel("test-key", httpx.AsyncClient(transport=httpx.MockTransport(network)))


def openrouter_chunk(text: str) -> str:
    return f"data: {json.dumps({'choices': [{'delta': {'content': text}}]})}"


def openrouter_stream(*chunks: str) -> str:
    """A streamed completion in the shape OpenRouter sends it."""
    lines = [": OPENROUTER PROCESSING", *map(openrouter_chunk, chunks), "data: [DONE]"]
    return "\n\n".join(lines) + "\n\n"


def test_chat_streams_the_reply_then_signals_done() -> None:
    events = chat(server(ScriptedModel([["Good ", "morning."]])), "Hello")

    assert events == [
        ("delta", {"text": "Good "}),
        ("delta", {"text": "morning."}),
        ("done", {}),
    ]


def test_every_model_request_requires_private_providers() -> None:
    sent: list[dict[str, Any]] = []

    def network(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, text=openrouter_stream("Good ", "morning."))

    client = server(openrouter_model(network))
    first = chat(client, "Hello")
    chat(client, "And again")

    assert [data["text"] for name, data in first if name == "delta"] == ["Good ", "morning."]
    assert len(sent) == 2
    for body in sent:
        assert body["provider"] == {"zdr": True, "data_collection": "deny"}
        assert body["stream"] is True


def test_a_rejected_request_ends_the_stream_with_a_readable_error() -> None:
    def network(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"code": 401, "message": "No auth credentials"}})

    events = chat(server(openrouter_model(network)), "Hello")

    assert events == [("error", {"message": "Model request failed (401): No auth credentials"})]


def test_an_error_partway_through_the_reply_is_reported() -> None:
    failure = {
        "error": {"code": "server_error", "message": "Provider disconnected unexpectedly"},
        "choices": [{"index": 0, "delta": {"content": ""}, "finish_reason": "error"}],
    }

    def network(request: httpx.Request) -> httpx.Response:
        body = f"{openrouter_chunk('Good ')}\n\ndata: {json.dumps(failure)}\n\n"
        return httpx.Response(200, text=body)

    events = chat(server(openrouter_model(network)), "Hello")

    assert events == [
        ("delta", {"text": "Good "}),
        ("error", {"message": "Model stopped mid-reply: Provider disconnected unexpectedly"}),
    ]


async def test_two_messages_at_once_are_answered_one_after_the_other() -> None:
    model = SlowScriptedModel([["Noted."], ["Tea."]])
    transport = httpx.ASGITransport(app=create_app(model))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await asyncio.gather(
            client.post("/api/chat", json={"message": "I like tea."}),
            client.post("/api/chat", json={"message": "What do I like?"}),
        )

    second_call = model.received[1]
    assert [m["content"] for m in second_call[1:]] == ["I like tea.", "Noted.", "What do I like?"]
