"""The activity screen's API: what the agent did, grouped into jobs, with feedback."""

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from scripted_model import ScriptedModel

from homunculus.activity import ActivityLog
from homunculus.approvals import Approvals
from homunculus.model import Message, ModelError, ToolCall
from homunculus.server import create_app
from homunculus.tools.files import file_tools


def server(model: ScriptedModel, tmp_path: Path) -> TestClient:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    app = create_app(
        model,
        file_tools(workspace),
        ActivityLog(tmp_path / "activity.jsonl"),
        Approvals(tmp_path / "approvals.json"),
    )
    return TestClient(app)


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
    ) -> AsyncIterator[str | ToolCall]:
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
    client = server(model, tmp_path)
    client.post("/api/chat", json={"message": "Email Sam"})
    [request] = client.get("/api/approvals").json()

    client.post(f"/api/approvals/{request['id']}/deny", json={"note": "Not now"})

    follow_up, original = client.get("/api/activity").json()
    assert follow_up["notice"] is True
    assert follow_up["message"].startswith("Declined:")
    assert original["notice"] is False
    assert original["steps"][0]["outcome"] == "waiting"
