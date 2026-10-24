"""Ratings, decisions and turn starts as OpenTelemetry log events, read from the logs file
the way any OTLP tool would.

The file follows OpenTelemetry's file exporter format: JSON lines, each one a `LogsData`
object in OTLP's JSON encoding. A rating follows the GenAI conventions'
`gen_ai.evaluation.result` event, with thumbs up and down mapped as Microsoft Foundry
documents for end-user feedback.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
from fastapi.testclient import TestClient
from scripted_model import ScriptedModel

from homunculus.activity import Activity
from homunculus.approvals import Approvals
from homunculus.model import Message, ToolCall, Usage
from homunculus.server import create_app
from homunculus.tools.files import file_tools


def app(model: ScriptedModel, tmp_path: Path) -> Any:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return create_app(
        model, file_tools(workspace), Activity(tmp_path), Approvals(tmp_path / "approvals.json")
    )


def value(any_value: dict[str, Any]) -> Any:
    """An OTLP AnyValue as a Python value. 64-bit integers are strings in OTLP JSON."""
    if "intValue" in any_value:
        return int(any_value["intValue"])
    return next(iter(any_value.values()))


def records(tmp_path: Path) -> list[dict[str, Any]]:
    found = []
    for line in (tmp_path / "logs.jsonl").read_text().splitlines():
        for resource in json.loads(line)["resourceLogs"]:
            for scope in resource["scopeLogs"]:
                for record in scope["logRecords"]:
                    attributes = {a["key"]: value(a["value"]) for a in record.get("attributes", [])}
                    found.append({**record, "attributes": attributes})
    return found


def named(found: list[dict[str, Any]], event: str) -> list[dict[str, Any]]:
    return [record for record in found if record.get("eventName") == event]


def root_span(tmp_path: Path) -> dict[str, Any]:
    for line in (tmp_path / "traces.jsonl").read_text().splitlines():
        for resource in json.loads(line)["resourceSpans"]:
            for span in resource["scopeSpans"][0]["spans"]:
                if span["name"] == "invoke_agent Homunculus":
                    return dict(span)
    raise AssertionError("no root span")


def test_a_rating_is_a_genai_evaluation_result_on_the_job_it_rates(tmp_path: Path) -> None:
    client = TestClient(app(ScriptedModel([["Hello."]]), tmp_path))
    client.post("/api/chat", json={"message": "Hi"})
    [job] = client.get("/api/activity").json()

    client.post(f"/api/activity/{job['job']}/feedback", json={"rating": "up"})
    client.post(
        f"/api/activity/{job['job']}/feedback", json={"rating": "down", "reason": "Too short"}
    )

    root = root_span(tmp_path)
    up, down = named(records(tmp_path), "gen_ai.evaluation.result")
    assert (up["traceId"], up["spanId"]) == (root["traceId"], root["spanId"])
    assert up["attributes"] == {
        "gen_ai.evaluation.name": "helpfulness",
        "gen_ai.evaluation.score.value": 1.0,
        "gen_ai.evaluation.score.label": "pass",
    }
    assert down["attributes"] == {
        "gen_ai.evaluation.name": "helpfulness",
        "gen_ai.evaluation.score.value": 0.0,
        "gen_ai.evaluation.score.label": "fail",
        "gen_ai.evaluation.explanation": "Too short",
    }


def test_a_decision_is_an_event_on_the_job_that_asked(tmp_path: Path) -> None:
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "old.md").write_text("Old")
    delete = ToolCall(id="call_1", name="delete", arguments='{"path": "old.md"}')
    client = TestClient(app(ScriptedModel([[delete], ["Asked."], ["Kept."]]), tmp_path))
    client.post("/api/chat", json={"message": "Delete old.md"})
    [request] = client.get("/api/approvals").json()

    client.post(f"/api/approvals/{request['id']}/deny", json={"note": "Keep it"})

    root = root_span(tmp_path)
    [decision] = named(records(tmp_path), "homunculus.approval.decision")
    assert (decision["traceId"], decision["spanId"]) == (root["traceId"], root["spanId"])
    assert decision["attributes"] == {
        "homunculus.approval.id": request["id"],
        "homunculus.approval.status": "denied",
        "homunculus.approval.note": "Keep it",
        "gen_ai.tool.name": "delete",
    }


class Waits(ScriptedModel):
    """A model that starts replying, then waits until the test lets it finish."""

    def __init__(self) -> None:
        super().__init__([])
        self.started = asyncio.Event()
        self.finish = asyncio.Event()

    async def stream(
        self, messages: list[Message], tools: list[dict[str, Any]]
    ) -> AsyncIterator[str | ToolCall | Usage]:
        yield "Working"
        self.started.set()
        await self.finish.wait()
        yield " on it."


async def test_a_running_job_shows_its_message_before_it_ends(tmp_path: Path) -> None:
    model = Waits()
    transport = httpx.ASGITransport(app=app(model, tmp_path))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        turn = asyncio.create_task(client.post("/api/chat", json={"message": "Plan my week"}))
        await model.started.wait()

        [job] = (await client.get("/api/activity")).json()

        model.finish.set()
        await turn
    [start] = named(records(tmp_path), "homunculus.turn.start")
    assert (job["message"], job["ended"]) == ("Plan my week", None)
    assert start["traceId"] == job["job"]
