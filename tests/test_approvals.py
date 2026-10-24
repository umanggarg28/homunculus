"""Approvals, through the server's HTTP API with a scripted model and stand-in tools."""

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import mcp_types as mcp
import pytest
from fastapi.testclient import TestClient
from scripted_model import ScriptedModel

from homunculus.activity import ActivityLog
from homunculus.approvals import Approvals
from homunculus.model import Message, ModelError, ToolCall
from homunculus.server import create_app
from homunculus.tools import Tool
from homunculus.tools.files import file_tools


class StandIn:
    """A tool that records every call it receives, so a test can see whether it ran."""

    def __init__(self, name: str, result: str) -> None:
        self.calls: list[dict[str, Any]] = []
        definition = mcp.Tool(
            name=name, description=f"Stand-in {name}.", input_schema={"type": "object"}
        )
        self.tool = Tool(definition, self._run)
        self._result = result

    def _run(self, arguments: dict[str, Any]) -> str:
        self.calls.append(arguments)
        return self._result


def asks_to(name: str, **arguments: object) -> ToolCall:
    return ToolCall(id=f"call_{name}", name=name, arguments=json.dumps(arguments))


@pytest.fixture
def send_email() -> StandIn:
    return StandIn("send_email", "Sent.")


def server(model: ScriptedModel, tmp_path: Path, *tools: StandIn) -> TestClient:
    app = create_app(
        model,
        [t.tool for t in tools],
        ActivityLog(tmp_path / "activity.jsonl"),
        Approvals(tmp_path / "approvals.json"),
    )
    return TestClient(app)


def test_an_action_that_needs_approval_waits_instead_of_running(
    tmp_path: Path, send_email: StandIn
) -> None:
    model = ScriptedModel([[asks_to("send_email", to="sam@example.com")], ["I have asked."]])
    client = server(model, tmp_path, send_email)

    client.post("/api/chat", json={"message": "Email Sam"})

    assert send_email.calls == []
    [pending] = client.get("/api/approvals").json()
    assert pending["tool"] == "send_email"
    assert json.loads(pending["arguments"]) == {"to": "sam@example.com"}
    assert pending["kind"] == "ask"
    assert pending["status"] == "pending"
    told = model.received[1][-1]["content"]
    assert told.startswith(f"Waiting for the operator's approval (request {pending['id']}).")


def test_a_waiting_step_is_reported_as_waiting(tmp_path: Path, send_email: StandIn) -> None:
    model = ScriptedModel([[asks_to("send_email", to="sam@example.com")], ["I have asked."]])
    client = server(model, tmp_path, send_email)

    body = client.post("/api/chat", json={"message": "Email Sam"}).text

    assert '"answer": "ask"' in body
    assert '"outcome": "waiting"' in body


def pending_id(client: TestClient) -> str:
    [pending] = [a for a in client.get("/api/approvals").json() if a["status"] == "pending"]
    return str(pending["id"])


def test_approving_runs_exactly_that_action_and_the_agent_carries_on(
    tmp_path: Path, send_email: StandIn
) -> None:
    model = ScriptedModel(
        [[asks_to("send_email", to="sam@example.com")], ["I have asked."], ["Sam is emailed."]]
    )
    client = server(model, tmp_path, send_email)
    client.post("/api/chat", json={"message": "Email Sam"})
    request = pending_id(client)

    decided = client.post(f"/api/approvals/{request}/approve").json()

    assert send_email.calls == [{"to": "sam@example.com"}]
    assert decided["status"] == "approved"
    assert decided["result"] == "Sent."
    told = model.received[2][-1]
    assert told["role"] == "user"
    assert told["content"] == (
        f'Approved: request {request}, send_email {{"to": "sam@example.com"}}. Result: Sent.'
    )
    assert client.get("/api/history").json()[-1] == {
        "role": "assistant",
        "content": "Sam is emailed.",
    }


def test_declining_never_runs_the_action_and_tells_the_agent_why(
    tmp_path: Path, send_email: StandIn
) -> None:
    model = ScriptedModel(
        [[asks_to("send_email", to="sam@example.com")], ["I have asked."], ["Understood."]]
    )
    client = server(model, tmp_path, send_email)
    client.post("/api/chat", json={"message": "Email Sam"})
    request = pending_id(client)

    decided = client.post(f"/api/approvals/{request}/deny", json={"note": "Not today"}).json()

    assert send_email.calls == []
    assert decided["status"] == "denied"
    assert model.received[2][-1]["content"] == (
        f'Declined: request {request}, send_email {{"to": "sam@example.com"}}. Reason: Not today'
    )


def test_a_hand_off_is_done_by_the_operator_and_the_agent_is_told(tmp_path: Path) -> None:
    pay = StandIn("pay", "Paid.")
    model = ScriptedModel([[asks_to("pay", amount=12)], ["Over to you."], ["Thanks."]])
    client = server(model, tmp_path, pay)
    client.post("/api/chat", json={"message": "Pay the invoice"})
    [request] = client.get("/api/approvals").json()
    assert request["kind"] == "hand off"

    decided = client.post(f"/api/approvals/{request['id']}/done", json={"note": "Paid by card"})

    assert pay.calls == []
    assert decided.json()["status"] == "done"
    assert model.received[2][-1]["content"] == (
        f'Done by the operator: request {request["id"]}, pay {{"amount": 12}}. Note: Paid by card'
    )


def test_a_request_is_decided_once_and_only_the_right_way(
    tmp_path: Path, send_email: StandIn
) -> None:
    model = ScriptedModel(
        [[asks_to("send_email", to="sam@example.com")], ["I have asked."], ["Sent."]]
    )
    client = server(model, tmp_path, send_email)
    client.post("/api/chat", json={"message": "Email Sam"})
    request = pending_id(client)

    assert client.post(f"/api/approvals/{request}/done").status_code == 409
    assert client.post(f"/api/approvals/{request}/approve").status_code == 200
    assert client.post(f"/api/approvals/{request}/approve").status_code == 409
    assert client.post(f"/api/approvals/{request}/deny").status_code == 409
    assert client.post("/api/approvals/nope/approve").status_code == 404
    assert send_email.calls == [{"to": "sam@example.com"}]


def test_pending_requests_survive_a_restart(tmp_path: Path, send_email: StandIn) -> None:
    model = ScriptedModel([[asks_to("send_email", to="sam@example.com")], ["I have asked."]])
    server(model, tmp_path, send_email).post("/api/chat", json={"message": "Email Sam"})

    restarted = server(ScriptedModel([]), tmp_path, send_email)

    assert [a["status"] for a in restarted.get("/api/approvals").json()] == ["pending"]


def test_the_decision_shows_in_history_as_a_notice(tmp_path: Path, send_email: StandIn) -> None:
    model = ScriptedModel(
        [[asks_to("send_email", to="sam@example.com")], ["I have asked."], ["Sam is emailed."]]
    )
    client = server(model, tmp_path, send_email)
    client.post("/api/chat", json={"message": "Email Sam"})
    request = pending_id(client)

    client.post(f"/api/approvals/{request}/approve")

    *_, notice, reply = client.get("/api/history").json()
    assert notice["role"] == "user"
    assert notice["notice"] is True
    assert notice["content"].startswith(f"Approved: request {request}")
    assert reply == {"role": "assistant", "content": "Sam is emailed."}


class FailingFollowUp(ScriptedModel):
    """Answers the chat turn from its script, then fails on the follow-up turn."""

    async def stream(
        self, messages: list[Message], tools: list[dict[str, Any]]
    ) -> AsyncIterator[str | ToolCall]:
        if len(self.received) >= 2:
            self.received.append(list(messages))
            raise ModelError("Model request failed (503): no providers available")
            yield ""  # makes this an async generator
        async for item in super().stream(messages, tools):
            yield item


def test_a_failed_follow_up_still_records_the_decision_and_says_so(
    tmp_path: Path, send_email: StandIn
) -> None:
    model = FailingFollowUp([[asks_to("send_email", to="sam@example.com")], ["I have asked."]])
    client = server(model, tmp_path, send_email)
    client.post("/api/chat", json={"message": "Email Sam"})
    request = pending_id(client)

    client.post(f"/api/approvals/{request}/approve")

    [decided] = client.get("/api/approvals").json()
    assert decided["follow_up"] == "failed: Model request failed (503): no providers available"
    assert (
        client.get("/api/history").json()[-1]["content"].startswith(f"Approved: request {request}")
    )


def test_a_successful_follow_up_is_recorded_as_replied(tmp_path: Path, send_email: StandIn) -> None:
    model = ScriptedModel(
        [[asks_to("send_email", to="sam@example.com")], ["I have asked."], ["Sam is emailed."]]
    )
    client = server(model, tmp_path, send_email)
    client.post("/api/chat", json={"message": "Email Sam"})
    request = pending_id(client)

    client.post(f"/api/approvals/{request}/approve")

    assert client.get("/api/approvals").json()[0]["follow_up"] == "replied"


def test_an_action_interrupted_by_a_restart_is_marked_unknown(tmp_path: Path) -> None:
    approvals = Approvals(tmp_path / "approvals.json")
    request = approvals.request("send_email", '{"to": "sam@example.com"}', "ask")
    approvals.decide(request.id, "approved")

    restarted = server(ScriptedModel([]), tmp_path)

    [decided] = restarted.get("/api/approvals").json()
    assert decided["result"] == (
        "Unknown: the server stopped while this action was running, so it may or may not have run."
    )


def test_a_delete_waits_for_approval_and_then_removes_the_file(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "old.md").write_text("Old notes")
    model = ScriptedModel([[asks_to("delete", path="old.md")], ["I have asked."], ["Deleted."]])
    app = create_app(
        model,
        file_tools(workspace),
        ActivityLog(tmp_path / "activity.jsonl"),
        Approvals(tmp_path / "approvals.json"),
    )
    client = TestClient(app)
    client.post("/api/chat", json={"message": "Delete old.md"})

    assert (workspace / "old.md").exists()
    decided = client.post(f"/api/approvals/{pending_id(client)}/approve").json()

    assert decided["result"] == "Deleted old.md."
    assert not (workspace / "old.md").exists()
