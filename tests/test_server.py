import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import mcp_types as mcp
from fastapi.testclient import TestClient
from scripted_model import ScriptedModel, SlowScriptedModel

from homunculus.activity import Activity
from homunculus.approvals import Approvals
from homunculus.model import Model, OpenRouterModel, ToolCall
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
    activity = Activity(tmp_path)
    approvals = Approvals(tmp_path / "approvals.json")
    return TestClient(create_app(model, tools or [], activity, approvals))


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


def test_only_the_api_fields_of_a_message_are_sent_to_the_model(tmp_path: Path) -> None:
    sent: list[dict[str, Any]] = []
    email = {
        "index": 0,
        "id": "call_1",
        "type": "function",
        "function": {"name": "send_email", "arguments": '{"to": "sam@example.com"}'},
    }

    def network(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        if len(sent) == 1:
            chunk = {"choices": [{"delta": {"tool_calls": [email]}}]}
            return httpx.Response(200, text=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n")
        return httpx.Response(200, text=openrouter_stream("Done."))

    send = Tool(
        mcp.Tool(name="send_email", description="Send an email.", input_schema={"type": "object"}),
        lambda arguments: "Sent.",
    )
    client = server(openrouter_model(network), tmp_path, [send])
    chat(client, "Email Sam")
    [request] = client.get("/api/approvals").json()

    client.post(f"/api/approvals/{request['id']}/approve")

    follow_up = sent[-1]["messages"]
    assert follow_up[-1]["content"].startswith("Approved:")
    assert all("notice" not in message for message in follow_up)


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


def test_tool_steps_are_streamed_with_the_reply(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write = ToolCall(id="call_1", name="write", arguments='{"path": "a.md", "content": "x"}')
    model = ScriptedModel([[write], ["Saved."]])

    events = chat(server(model, tmp_path, file_tools(workspace)), "Save a.md")

    name, step = events[0]
    assert name == "tool"
    assert step.pop("duration_ms") >= 0
    assert step == {
        "name": "write",
        "arguments": '{"path": "a.md", "content": "x"}',
        "answer": "act",
        "result": "Wrote a.md.",
        "outcome": "ok",
    }
    assert events[1:] == [("delta", {"text": "Saved."}), ("done", {})]


def test_history_lists_the_conversation_in_words(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write = ToolCall(id="call_1", name="write", arguments='{"path": "a.md", "content": "x"}')
    client = server(ScriptedModel([[write], ["Saved."]]), tmp_path, file_tools(workspace))
    chat(client, "Save a.md")

    response = client.get("/api/history")

    assert response.json() == [
        {"role": "user", "content": "Save a.md"},
        {"role": "assistant", "content": "Saved."},
    ]


def test_the_built_console_is_served_with_its_routes(tmp_path: Path) -> None:
    console = tmp_path / "console"
    (console / "assets").mkdir(parents=True)
    (console / "index.html").write_text("<html>console</html>")
    (console / "assets" / "app.js").write_text("console.log(1)")
    activity = Activity(tmp_path)
    client = TestClient(
        create_app(ScriptedModel([]), [], activity, Approvals(tmp_path / "a.json"), console=console)
    )

    assert client.get("/").text == "<html>console</html>"
    assert client.get("/chat").text == "<html>console</html>"
    assert client.get("/assets/app.js").text == "console.log(1)"
    assert client.get("/api/history").json() == []
    assert client.get("/api/unknown").status_code == 404
    assert client.get("/api").status_code == 404


def test_a_console_build_without_assets_still_starts(tmp_path: Path) -> None:
    console = tmp_path / "console"
    console.mkdir()
    (console / "index.html").write_text("<html>console</html>")
    activity = Activity(tmp_path)

    client = TestClient(
        create_app(ScriptedModel([]), [], activity, Approvals(tmp_path / "a.json"), console=console)
    )

    assert client.get("/chat").text == "<html>console</html>"


def test_an_unreachable_model_ends_the_stream_with_a_readable_error(tmp_path: Path) -> None:
    def network(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    events = chat(server(openrouter_model(network), tmp_path), "Hello")

    assert events == [("error", {"message": "Could not reach the model: connection refused"})]
