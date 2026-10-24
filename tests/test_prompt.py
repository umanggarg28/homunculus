"""What the model is told on every turn, seen through the server's HTTP API."""

from pathlib import Path

from fastapi.testclient import TestClient
from scripted_model import ScriptedModel

from homunculus.activity import Activity
from homunculus.approvals import Approvals
from homunculus.server import create_app
from homunculus.tools.files import file_tools


def server(model: ScriptedModel, tmp_path: Path) -> TestClient:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_app(
        model,
        file_tools(workspace),
        Activity(tmp_path),
        Approvals(tmp_path / "approvals.json"),
    )
    return TestClient(app)


def headings(text: str) -> list[str]:
    return [line.removeprefix("## ") for line in text.splitlines() if line.startswith("## ")]


def test_every_turn_starts_with_the_fixed_instructions_in_order(tmp_path: Path) -> None:
    model = ScriptedModel([["Hello."]])
    server(model, tmp_path).post("/api/chat", json={"message": "Hi"})

    first = model.received[0][0]

    assert first["role"] == "system"
    assert headings(first["content"]) == [
        "Who you are",
        "When to ask",
        "What you can and cannot do",
    ]


def test_the_fixed_instructions_are_the_same_on_every_turn(tmp_path: Path) -> None:
    model = ScriptedModel([["Hello."], ["Still here."]])
    client = server(model, tmp_path)

    client.post("/api/chat", json={"message": "Hi"})
    client.post("/api/chat", json={"message": "Are you there?"})

    # The same opening text lets a provider reuse its cached work on that part.
    assert model.received[0][0] == model.received[1][0]


def test_every_tool_offered_says_when_to_use_it(tmp_path: Path) -> None:
    model = ScriptedModel([["Hello."]])
    server(model, tmp_path).post("/api/chat", json={"message": "Hi"})

    for tool in model.offered_tools[0]:
        description = tool["function"]["description"]
        assert any(word in description for word in ("Use ", "Only ")), tool["function"]["name"]
