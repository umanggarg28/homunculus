import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from fastapi.testclient import TestClient
from scripted_model import ScriptedModel, SlowScriptedModel

from homunculus.activity import ActivityLog
from homunculus.model import Model, OpenRouterModel
from homunculus.server import create_app
from homunculus.tools import Tool
from homunculus.tools.files import file_tools


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


def server(model: Model, tmp_path: Path, tools: list[Tool] | None = None) -> TestClient:
    return TestClient(create_app(model, tools or [], ActivityLog(tmp_path / "activity.jsonl")))


def openrouter_model(network: Callable[[httpx.Request], httpx.Response]) -> OpenRouterModel:
    """The real model module, with only the network replaced by `network`."""
    return OpenRouterModel("test-key", httpx.AsyncClient(transport=httpx.MockTransport(network)))


def openrouter_chunk(text: str) -> str:
    return f"data: {json.dumps({'choices': [{'delta': {'content': text}}]})}"


def openrouter_stream(*chunks: str) -> str:
    """A streamed completion in the shape OpenRouter sends it."""
    lines = [": OPENROUTER PROCESSING", *map(openrouter_chunk, chunks), "data: [DONE]"]
    return "\n\n".join(lines) + "\n\n"


def test_chat_streams_the_reply_then_signals_done(tmp_path: Path) -> None:
    events = chat(server(ScriptedModel([["Good ", "morning."]]), tmp_path), "Hello")

    assert events == [
        ("delta", {"text": "Good "}),
        ("delta", {"text": "morning."}),
        ("done", {}),
    ]


def test_every_model_request_requires_private_providers(tmp_path: Path) -> None:
    sent: list[dict[str, Any]] = []

    def network(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, text=openrouter_stream("Good ", "morning."))

    client = server(openrouter_model(network), tmp_path)
    first = chat(client, "Hello")
    chat(client, "And again")

    assert [data["text"] for name, data in first if name == "delta"] == ["Good ", "morning."]
    assert len(sent) == 2
    for body in sent:
        assert body["provider"] == {"zdr": True, "data_collection": "deny"}
        assert body["stream"] is True


def test_a_rejected_request_ends_the_stream_with_a_readable_error(tmp_path: Path) -> None:
    def network(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"code": 401, "message": "No auth credentials"}})

    events = chat(server(openrouter_model(network), tmp_path), "Hello")

    assert events == [("error", {"message": "Model request failed (401): No auth credentials"})]


def test_an_error_partway_through_the_reply_is_reported(tmp_path: Path) -> None:
    failure = {
        "error": {"code": "server_error", "message": "Provider disconnected unexpectedly"},
        "choices": [{"index": 0, "delta": {"content": ""}, "finish_reason": "error"}],
    }

    def network(request: httpx.Request) -> httpx.Response:
        body = f"{openrouter_chunk('Good ')}\n\ndata: {json.dumps(failure)}\n\n"
        return httpx.Response(200, text=body)

    events = chat(server(openrouter_model(network), tmp_path), "Hello")

    assert events == [
        ("delta", {"text": "Good "}),
        ("error", {"message": "Model stopped mid-reply: Provider disconnected unexpectedly"}),
    ]


async def test_two_messages_at_once_are_answered_one_after_the_other(tmp_path: Path) -> None:
    model = SlowScriptedModel([["Noted."], ["Tea."]])
    transport = httpx.ASGITransport(app=server(model, tmp_path).app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await asyncio.gather(
            client.post("/api/chat", json={"message": "I like tea."}),
            client.post("/api/chat", json={"message": "What do I like?"}),
        )

    second_call = model.received[1]
    assert [m["content"] for m in second_call[1:]] == ["I like tea.", "Noted.", "What do I like?"]


def openrouter_tool_call_stream() -> str:
    """A tool call as OpenRouter streams it: the arguments arrive in fragments."""
    pieces = [
        {
            "tool_calls": [
                {
                    "index": 0,
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "write", "arguments": ""},
                }
            ]
        },
        {"tool_calls": [{"index": 0, "function": {"arguments": '{"path": "tea.md", '}}]},
        {"tool_calls": [{"index": 0, "function": {"arguments": '"content": "Green"}'}}]},
    ]
    lines = [f"data: {json.dumps({'choices': [{'delta': piece}]})}" for piece in pieces]
    lines.append(f"data: {json.dumps({'choices': [{'delta': {}, 'finish_reason': 'tool_calls'}]})}")
    lines.append("data: [DONE]")
    return "\n\n".join(lines) + "\n\n"


def test_the_real_model_offers_tools_and_runs_the_one_it_asks_for(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sent: list[dict[str, Any]] = []

    def network(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        if len(sent) == 1:
            return httpx.Response(200, text=openrouter_tool_call_stream())
        return httpx.Response(200, text=openrouter_stream("Saved."))

    client = server(openrouter_model(network), tmp_path, file_tools(workspace))
    events = chat(client, "Note that I like green tea.")

    assert (workspace / "tea.md").read_text() == "Green"
    assert events[-2:] == [("delta", {"text": "Saved."}), ("done", {})]
    offered = {tool["function"]["name"] for tool in sent[0]["tools"]}
    assert offered == {"read", "write", "edit", "list", "search", "delete", "rename"}
    assert sent[1]["messages"][-1] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": "Wrote tea.md.",
    }
