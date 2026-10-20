import json
from typing import Any

import httpx
from fastapi.testclient import TestClient
from scripted_model import ScriptedModel

from homunculus.model import Model, OpenRouterModel
from homunculus.server import create_app


def chat(model: Model, text: str) -> list[tuple[str, dict[str, Any]]]:
    """Send one chat message and return the streamed events as (name, data) pairs."""
    client = TestClient(create_app(model))
    response = client.post("/api/chat", json={"message": text})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = []
    for block in response.text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((fields["event"], json.loads(fields["data"])))
    return events


def test_chat_streams_the_reply_then_signals_done() -> None:
    events = chat(ScriptedModel([["Good ", "morning."]]), "Hello")

    assert events == [
        ("delta", {"text": "Good "}),
        ("delta", {"text": "morning."}),
        ("done", {}),
    ]


def openrouter_stream(*chunks: str) -> str:
    """A streamed completion in the shape OpenRouter sends it."""
    lines = [": OPENROUTER PROCESSING"]
    lines += [f"data: {json.dumps({'choices': [{'delta': {'content': c}}]})}" for c in chunks]
    lines.append("data: [DONE]")
    return "\n\n".join(lines) + "\n\n"


def test_every_model_request_requires_private_providers() -> None:
    sent: list[dict[str, Any]] = []

    def network(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, text=openrouter_stream("Good ", "morning."))

    model = OpenRouterModel("test-key", httpx.AsyncClient(transport=httpx.MockTransport(network)))

    events = chat(model, "Hello")

    assert [data["text"] for name, data in events if name == "delta"] == ["Good ", "morning."]
    assert sent[0]["provider"] == {"zdr": True, "data_collection": "deny"}
    assert sent[0]["model"] == "z-ai/glm-5.3-flash"
    assert sent[0]["stream"] is True


def test_a_rejected_request_ends_the_stream_with_a_readable_error() -> None:
    def network(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"code": 401, "message": "No auth credentials"}})

    model = OpenRouterModel("bad-key", httpx.AsyncClient(transport=httpx.MockTransport(network)))

    events = chat(model, "Hello")

    assert events == [("error", {"message": "Model request failed (401): No auth credentials"})]


def test_an_error_partway_through_the_reply_is_reported_not_hidden() -> None:
    failure = {
        "error": {"code": "server_error", "message": "Provider disconnected unexpectedly"},
        "choices": [{"index": 0, "delta": {"content": ""}, "finish_reason": "error"}],
    }

    def network(request: httpx.Request) -> httpx.Response:
        body = f"data: {json.dumps({'choices': [{'delta': {'content': 'Good '}}]})}\n\n"
        body += f"data: {json.dumps(failure)}\n\n"
        return httpx.Response(200, text=body)

    model = OpenRouterModel("test-key", httpx.AsyncClient(transport=httpx.MockTransport(network)))

    events = chat(model, "Hello")

    assert events == [
        ("delta", {"text": "Good "}),
        ("error", {"message": "Model stopped mid-reply: Provider disconnected unexpectedly"}),
    ]
