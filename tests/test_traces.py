"""The activity log as OpenTelemetry traces, read from the file the way any OTLP tool would.

The file follows OpenTelemetry's file exporter format: JSON lines, each one a `TracesData`
object in OTLP's JSON encoding. Span names and attributes follow the GenAI conventions.
"""

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from scripted_model import ScriptedModel

from homunculus.activity import Activity
from homunculus.approvals import Approvals
from homunculus.model import ModelError, ToolCall, Usage
from homunculus.server import create_app
from homunculus.tools.files import file_tools

INTERNAL, CLIENT = 1, 3  # OTLP span kinds
STATUS_ERROR = 2


def server(model: ScriptedModel, tmp_path: Path) -> TestClient:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    app = create_app(
        model, file_tools(workspace), Activity(tmp_path), Approvals(tmp_path / "approvals.json")
    )
    return TestClient(app, raise_server_exceptions=False)


def value(any_value: dict[str, Any]) -> Any:
    """An OTLP AnyValue as a Python value. 64-bit integers are strings in OTLP JSON."""
    if "intValue" in any_value:
        return int(any_value["intValue"])
    return next(iter(any_value.values()))


def spans(tmp_path: Path) -> list[dict[str, Any]]:
    found = []
    for line in (tmp_path / "traces.jsonl").read_text().splitlines():
        for resource in json.loads(line)["resourceSpans"]:
            for scope in resource["scopeSpans"]:
                for span in scope["spans"]:
                    attributes = {a["key"]: value(a["value"]) for a in span.get("attributes", [])}
                    found.append({**span, "attributes": attributes})
    return found


def named(found: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [span for span in found if span["name"] == name]


def write(path: str) -> ToolCall:
    return ToolCall(id="call_1", name="write", arguments=json.dumps({"path": path, "content": "x"}))


def test_a_turn_is_one_trace_with_a_span_for_each_model_call_and_tool(tmp_path: Path) -> None:
    server(ScriptedModel([[write("a.md")], ["Saved."]]), tmp_path).post(
        "/api/chat", json={"message": "Save a.md"}
    )

    found = spans(tmp_path)
    [root] = named(found, "invoke_agent Homunculus")
    [tool] = named(found, "execute_tool write")
    chats = named(found, "chat scripted")

    assert len(found) == 4 and len(chats) == 2
    assert {span["traceId"] for span in found} == {root["traceId"]}
    assert len(root["traceId"]) == 32 and len(root["spanId"]) == 16  # hex, as OTLP JSON says
    assert all(span["parentSpanId"] == root["spanId"] for span in [tool, *chats])
    assert (root["kind"], tool["kind"], chats[0]["kind"]) == (INTERNAL, INTERNAL, CLIENT)


def test_spans_carry_the_genai_attributes(tmp_path: Path) -> None:
    server(ScriptedModel([[write("a.md")], ["Saved."]]), tmp_path).post(
        "/api/chat", json={"message": "Save a.md"}
    )

    found = spans(tmp_path)
    [root] = named(found, "invoke_agent Homunculus")
    [tool] = named(found, "execute_tool write")

    assert root["attributes"]["gen_ai.operation.name"] == "invoke_agent"
    assert root["attributes"]["gen_ai.agent.name"] == "Homunculus"
    assert root["attributes"]["gen_ai.conversation.id"]
    assert tool["attributes"] == {
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.tool.name": "write",
        "gen_ai.tool.type": "function",
        "gen_ai.tool.call.id": "call_1",
        "gen_ai.tool.call.arguments": '{"path": "a.md", "content": "x"}',
        "gen_ai.tool.call.result": "Wrote a.md.",
        "homunculus.gate.answer": "act",
        "homunculus.outcome": "ok",
    }


def test_each_model_call_records_its_token_counts(tmp_path: Path) -> None:
    usage = Usage(input_tokens=120, output_tokens=8, cached_input_tokens=100)
    server(ScriptedModel([["Hello.", usage]]), tmp_path).post("/api/chat", json={"message": "Hi"})

    [chat] = named(spans(tmp_path), "chat scripted")

    assert chat["attributes"]["gen_ai.operation.name"] == "chat"
    assert chat["attributes"]["gen_ai.usage.input_tokens"] == 120
    assert chat["attributes"]["gen_ai.usage.output_tokens"] == 8
    assert chat["attributes"]["gen_ai.usage.cache_read.input_tokens"] == 100


class Unreachable(ScriptedModel):
    async def stream(self, messages, tools):  # type: ignore[no-untyped-def]
        raise ModelError("Could not reach the model: connection refused")
        yield ""


def test_a_failed_turn_ends_its_trace_with_an_error_status(tmp_path: Path) -> None:
    server(Unreachable([]), tmp_path).post("/api/chat", json={"message": "Hi"})

    [root] = named(spans(tmp_path), "invoke_agent Homunculus")

    assert root["status"]["code"] == STATUS_ERROR
    assert root["status"]["message"] == "Could not reach the model: connection refused"
    assert root["attributes"]["error.type"] == "ModelError"


def test_a_model_call_records_what_the_model_said_and_asked_for(tmp_path: Path) -> None:
    server(ScriptedModel([["Let me save it.", write("a.md")], ["Saved."]]), tmp_path).post(
        "/api/chat", json={"message": "Save a.md"}
    )

    first, second = named(spans(tmp_path), "chat scripted")

    [said] = json.loads(first["attributes"]["gen_ai.output.messages"])
    assert said["role"] == "assistant" and said["finish_reason"] == "tool_call"
    assert said["parts"] == [
        {"type": "text", "content": "Let me save it."},
        {
            "type": "tool_call",
            "id": "call_1",
            "name": "write",
            "arguments": {"path": "a.md", "content": "x"},
        },
    ]
    [replied] = json.loads(second["attributes"]["gen_ai.output.messages"])
    assert replied["finish_reason"] == "stop"


def test_a_failed_tool_call_has_an_error_status_and_type(tmp_path: Path) -> None:
    read = ToolCall(id="call_1", name="read", arguments='{"path": "missing.md"}')
    server(ScriptedModel([[read], ["Not there."]]), tmp_path).post(
        "/api/chat", json={"message": "Read missing.md"}
    )

    [tool] = named(spans(tmp_path), "execute_tool read")

    assert tool["status"]["code"] == STATUS_ERROR
    assert tool["attributes"]["error.type"] == "tool_error"


def test_every_turn_carries_the_same_conversation_id(tmp_path: Path) -> None:
    client = server(ScriptedModel([["Hello."], ["Again."]]), tmp_path)
    client.post("/api/chat", json={"message": "Hi"})
    client.post("/api/chat", json={"message": "Hi again"})

    roots = named(spans(tmp_path), "invoke_agent Homunculus")

    assert len({root["traceId"] for root in roots}) == 2
    assert len({root["attributes"]["gen_ai.conversation.id"] for root in roots}) == 1


def test_an_approved_action_runs_in_the_trace_that_asked_for_it(tmp_path: Path) -> None:
    delete = ToolCall(id="call_7", name="delete", arguments='{"path": "old.md"}')
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "old.md").write_text("Old")
    client = server(ScriptedModel([[delete], ["I have asked."], ["Deleted."]]), tmp_path)
    client.post("/api/chat", json={"message": "Delete old.md"})
    [request] = client.get("/api/approvals").json()

    client.post(f"/api/approvals/{request['id']}/approve")

    found = spans(tmp_path)
    [asked, ran] = named(found, "execute_tool delete")
    root = next(
        s for s in named(found, "invoke_agent Homunculus") if s["traceId"] == asked["traceId"]
    )
    assert ran["traceId"] == root["traceId"] and ran["parentSpanId"] == root["spanId"]
    assert ran["attributes"]["gen_ai.tool.call.id"] == "call_7"
    assert ran["attributes"]["homunculus.gate.answer"] == "ask"
    assert (asked["attributes"]["homunculus.outcome"], ran["attributes"]["homunculus.outcome"]) == (
        "waiting",
        "ok",
    )
    decisions = [
        json.loads(line) for line in (tmp_path / "operator.jsonl").read_text().splitlines()
    ]
    assert [(d["kind"], d["job"], d["status"]) for d in decisions] == [
        ("decision", root["traceId"], "approved")
    ]
