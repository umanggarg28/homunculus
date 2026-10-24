"""The activity screen's API: what the agent did, grouped into jobs, with feedback."""

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import mcp_types as mcp
from fastapi.testclient import TestClient
from scripted_model import ScriptedModel

from homunculus.activity import Activity
from homunculus.approvals import Approvals
from homunculus.model import Message, ModelError, ToolCall, Usage
from homunculus.server import create_app
from homunculus.tools import Tool
from homunculus.tools.files import file_tools

# A stand-in outward tool. It has no annotations, so by the protocol's defaults it asks.
SEND_EMAIL = Tool(mcp.Tool(name="send_email", input_schema={"type": "object"}), lambda _: "Sent.")


def server(model: ScriptedModel, tmp_path: Path, *extra: Tool) -> TestClient:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    app = create_app(
        model,
        [*file_tools(workspace), *extra],
        Activity(tmp_path),
        Approvals(tmp_path / "approvals.json"),
    )
    # A server error is checked through what the log shows, not raised in the test.
    return TestClient(app, raise_server_exceptions=False)


def write(path: str, content: str) -> ToolCall:
    return ToolCall(
        id="call_1", name="write", arguments=json.dumps({"path": path, "content": content})
    )


def test_each_message_and_everything_done_for_it_is_one_job(tmp_path: Path) -> None:
    model = ScriptedModel([[write("tea.md", "Green")], ["Saved."], ["Hello."]])
    client = server(model, tmp_path)
    client.post("/api/chat", json={"message": "Save tea.md"})
    client.post("/api/chat", json={"message": "Hi"})

    newest, oldest = client.get("/api/activity").json()

    assert newest["message"] == "Hi"
    assert newest["reply"] == "Hello."
    assert newest["steps"] == []
    assert oldest["message"] == "Save tea.md"
    assert oldest["reply"] == "Saved."
    [step] = oldest["steps"]
    assert (step["name"], step["answer"], step["outcome"], step["result"]) == (
        "write",
        "act",
        "ok",
        "Wrote tea.md.",
    )
    assert oldest["feedback"] is None


def test_feedback_is_kept_with_the_job_and_the_latest_one_counts(tmp_path: Path) -> None:
    client = server(ScriptedModel([["Hello."]]), tmp_path)
    client.post("/api/chat", json={"message": "Hi"})
    [job] = client.get("/api/activity").json()

    first = client.post(f"/api/activity/{job['job']}/feedback", json={"rating": "up"})
    client.post(
        f"/api/activity/{job['job']}/feedback", json={"rating": "down", "reason": "Too short"}
    )

    assert first.status_code == 200
    [job] = client.get("/api/activity").json()
    assert (job["feedback"]["rating"], job["feedback"]["reason"]) == ("down", "Too short")
    restarted = server(ScriptedModel([]), tmp_path)
    assert restarted.get("/api/activity").json()[0]["feedback"]["rating"] == "down"


def test_feedback_must_name_a_real_job_and_a_rating_of_up_or_down(tmp_path: Path) -> None:
    client = server(ScriptedModel([["Hello."]]), tmp_path)
    client.post("/api/chat", json={"message": "Hi"})
    [job] = client.get("/api/activity").json()

    assert client.post("/api/activity/nope/feedback", json={"rating": "up"}).status_code == 404
    bad = client.post(f"/api/activity/{job['job']}/feedback", json={"rating": "meh"})
    assert bad.status_code == 422


class Unreachable(ScriptedModel):
    async def stream(
        self, messages: list[Message], tools: list[dict[str, Any]]
    ) -> AsyncIterator[str | ToolCall | Usage]:
        raise ModelError("Could not reach the model: connection refused")
        yield ""  # makes this an async generator


def test_a_failed_reply_shows_as_a_job_that_ended_in_an_error(tmp_path: Path) -> None:
    client = server(Unreachable([]), tmp_path)
    client.post("/api/chat", json={"message": "Hi"})

    [job] = client.get("/api/activity").json()

    assert job["reply"] is None
    assert job["error"] == "Could not reach the model: connection refused"
    assert job["ended"] is not None


def test_the_turn_that_tells_the_agent_a_decision_is_marked_as_a_notice(tmp_path: Path) -> None:
    send = ToolCall(id="call_1", name="send_email", arguments='{"to": "sam@example.com"}')
    model = ScriptedModel([[send], ["I have asked."], ["Sent, thanks."]])
    client = server(model, tmp_path, SEND_EMAIL)
    client.post("/api/chat", json={"message": "Email Sam"})
    [request] = client.get("/api/approvals").json()

    client.post(f"/api/approvals/{request['id']}/deny", json={"note": "Not now"})

    follow_up, original = client.get("/api/activity").json()
    assert follow_up["notice"] is True
    assert follow_up["message"].startswith("Declined:")
    assert original["notice"] is False
    assert original["steps"][0]["outcome"] == "waiting"


def test_an_approved_action_shows_in_the_job_that_asked_for_it(tmp_path: Path) -> None:
    send = ToolCall(id="call_1", name="send_email", arguments='{"to": "sam@example.com"}')
    model = ScriptedModel([[send], ["I have asked."], ["Sent, thanks."]])
    client = server(model, tmp_path, SEND_EMAIL)
    client.post("/api/chat", json={"message": "Email Sam"})
    [request] = client.get("/api/approvals").json()

    client.post(f"/api/approvals/{request['id']}/approve")

    _, original = client.get("/api/activity").json()
    asked, ran = original["steps"]
    assert (asked["outcome"], asked["request"]) == ("waiting", request["id"])
    assert (ran["outcome"], ran["result"], ran["request"]) == ("ok", "Sent.", request["id"])


class Breaks(ScriptedModel):
    async def stream(
        self, messages: list[Message], tools: list[dict[str, Any]]
    ) -> AsyncIterator[str | ToolCall | Usage]:
        yield "Let me"
        raise RuntimeError("a bug")


def test_a_turn_cut_short_by_an_unexpected_failure_still_ends(tmp_path: Path) -> None:
    client = server(Breaks([]), tmp_path)
    client.post("/api/chat", json={"message": "Hi"})

    [job] = client.get("/api/activity").json()

    assert job["error"] == "The turn stopped before it finished (RuntimeError: a bug)."
    assert job["ended"] is not None


class Cancelled(ScriptedModel):
    async def stream(
        self, messages: list[Message], tools: list[dict[str, Any]]
    ) -> AsyncIterator[str | ToolCall | Usage]:
        yield "Let me"
        raise asyncio.CancelledError("Cancelled via cancel scope 10b6a9090 by <Task pending>")


def test_a_turn_cut_short_by_a_closed_connection_says_so_plainly(tmp_path: Path) -> None:
    client = server(Cancelled([]), tmp_path)
    try:
        client.post("/api/chat", json={"message": "Hi"})
    except asyncio.CancelledError:
        pass  # the test client passes the cancellation on, as a real server would

    [job] = client.get("/api/activity").json()

    assert job["error"] == "The turn stopped before it finished: the connection closed."
