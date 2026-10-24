"""File tools, exercised through the loop with a scripted model."""

import json
from pathlib import Path

import mcp_types as mcp
import pytest
from fastapi.testclient import TestClient
from scripted_model import ScriptedModel

from homunculus import loop
from homunculus.activity import ActivityLog
from homunculus.approvals import Approvals
from homunculus.model import Message, ToolCall
from homunculus.server import create_app
from homunculus.tools import Tool, result_text
from homunculus.tools.files import file_tools


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """An empty workspace folder. The activity log sits beside it, outside the workspace."""
    folder = tmp_path / "workspace"
    folder.mkdir()
    return folder


def tool_call(name: str, **arguments: object) -> ToolCall:
    return ToolCall(id=f"call_{name}", name=name, arguments=json.dumps(arguments))


async def run_job(model: ScriptedModel, workspace: Path, text: str) -> str:
    activity = ActivityLog(workspace.parent / "activity.jsonl")
    history: list[Message] = []
    approvals = Approvals(workspace.parent / "approvals.json")
    tools = file_tools(workspace)
    items = [item async for item in loop.run(model, tools, activity, approvals, history, text)]
    return "".join(item for item in items if isinstance(item, str))


def tool_result(model: ScriptedModel, call: int = 1) -> str:
    """The tool result the model was shown at the start of its `call`-th turn."""
    last = model.received[call][-1]
    assert last["role"] == "tool"
    return str(last["content"])


async def test_the_agent_can_write_a_file_in_its_workspace(workspace: Path) -> None:
    model = ScriptedModel(
        [[tool_call("write", path="notes/tea.md", content="I like tea.\n")], ["Saved."]]
    )

    reply = await run_job(model, workspace, "Note that I like tea.")

    assert (workspace / "notes" / "tea.md").read_text() == "I like tea.\n"
    assert reply == "Saved."
    assert len(model.received) == 2


async def test_reading_a_file_shows_numbered_lines(workspace: Path) -> None:
    (workspace / "plan.md").write_text("Buy tea\nCall home\n")
    model = ScriptedModel([[tool_call("read", path="plan.md")], ["Two things."]])

    await run_job(model, workspace, "What is on my plan?")

    assert tool_result(model) == "1\tBuy tea\n2\tCall home"


async def test_reading_a_missing_file_tells_the_model(workspace: Path) -> None:
    model = ScriptedModel([[tool_call("read", path="nope.md")], ["It does not exist."]])

    await run_job(model, workspace, "Read nope.md")

    assert tool_result(model) == "Error: nope.md does not exist."


@pytest.mark.parametrize("path", ["../outside.md", "/tmp/outside.md", "notes/../../outside.md"])
async def test_a_path_outside_the_workspace_is_refused_by_the_gate(
    tmp_path: Path, workspace: Path, path: str
) -> None:
    model = ScriptedModel([[tool_call("write", path=path, content="escaped")], ["Done."]])

    await run_job(model, workspace, "Write outside")

    assert tool_result(model) == "Not done: the gate answered 'refuse' for this action."
    assert not (tmp_path / "outside.md").exists()


@pytest.mark.parametrize("path", ["../outside.md", "/tmp/outside.md", "notes/../../outside.md"])
def test_the_file_tools_refuse_a_path_outside_the_workspace_on_their_own(
    tmp_path: Path, workspace: Path, path: str
) -> None:
    """A second fence behind the gate, so one mistake in either cannot open the workspace."""
    write = next(tool for tool in file_tools(workspace) if tool.name == "write")

    result = write.call({"path": path, "content": "escaped"})

    assert result.is_error
    assert "is outside the workspace" in result_text(result)
    assert not (tmp_path / "outside.md").exists()


async def test_no_tool_can_reach_the_activity_log(tmp_path: Path, workspace: Path) -> None:
    model = ScriptedModel(
        [[tool_call("write", path="../activity.jsonl", content="forged")], ["Done."]]
    )

    await run_job(model, workspace, "Rewrite the log")

    entries = ActivityLog(tmp_path / "activity.jsonl").entries()
    assert [(entry["kind"], entry.get("answer")) for entry in entries] == [
        ("message", None),
        ("tool", "refuse"),
        ("reply", None),
    ]


async def test_a_tool_that_does_not_exist_is_refused(tmp_path: Path, workspace: Path) -> None:
    model = ScriptedModel(
        [[ToolCall(id="call_1", name="search", arguments='{"path": "."}')], ["Sorry."]]
    )
    activity = ActivityLog(tmp_path / "activity.jsonl")
    approvals = Approvals(tmp_path / "approvals.json")
    no_tools: list[Tool] = []

    _ = [chunk async for chunk in loop.run(model, no_tools, activity, approvals, [], "Search")]

    assert tool_result(model) == "Error: there is no tool named search."


async def test_a_link_that_leads_outside_the_workspace_is_refused(
    tmp_path: Path, workspace: Path
) -> None:
    (tmp_path / "private.md").write_text("secret")
    (workspace / "shortcut.md").symlink_to(tmp_path / "private.md")
    model = ScriptedModel([[tool_call("read", path="shortcut.md")], ["Refused."]])

    await run_job(model, workspace, "Read the shortcut")

    assert tool_result(model) == "Error: shortcut.md is outside the workspace."


async def test_listing_shows_folders_and_files(workspace: Path) -> None:
    (workspace / "notes").mkdir()
    (workspace / "plan.md").write_text("Buy tea\n")
    (workspace / "notes" / "tea.md").write_text("Green\n")
    model = ScriptedModel([[tool_call("list", path=".")], ["Here you go."]])

    await run_job(model, workspace, "What is in my workspace?")

    assert tool_result(model) == "notes/\nplan.md"


async def test_editing_replaces_text_that_appears_once(workspace: Path) -> None:
    (workspace / "plan.md").write_text("Buy tea\nCall home\n")
    model = ScriptedModel(
        [[tool_call("edit", path="plan.md", old="Buy tea", new="Buy green tea")], ["Changed."]]
    )

    await run_job(model, workspace, "Make it green tea")

    assert (workspace / "plan.md").read_text() == "Buy green tea\nCall home\n"


async def test_editing_text_that_is_not_unique_changes_nothing(workspace: Path) -> None:
    (workspace / "plan.md").write_text("tea\ntea\n")
    model = ScriptedModel([[tool_call("edit", path="plan.md", old="tea", new="coffee")], ["Hm."]])

    await run_job(model, workspace, "Change tea")

    assert (workspace / "plan.md").read_text() == "tea\ntea\n"
    assert tool_result(model) == "Error: the text appears 2 times in plan.md; it must appear once."


async def test_search_finds_matching_lines_across_files(workspace: Path) -> None:
    (workspace / "notes").mkdir()
    (workspace / "plan.md").write_text("Buy Tea\nCall home\n")
    (workspace / "notes" / "drinks.md").write_text("Coffee\nGreen tea is best\n")
    model = ScriptedModel([[tool_call("search", path=".", text="tea")], ["Found it."]])

    await run_job(model, workspace, "Where do I mention tea?")

    assert tool_result(model) == "notes/drinks.md:2: Green tea is best\nplan.md:1: Buy Tea"


async def test_an_action_that_needs_approval_is_not_run(tmp_path: Path, workspace: Path) -> None:
    ran: list[dict[str, object]] = []

    def send_email(arguments: dict[str, object]) -> str:
        ran.append(arguments)
        return "Sent."

    send = Tool(
        mcp.Tool(name="send_email", description="Send an email.", input_schema={"type": "object"}),
        send_email,
    )
    model = ScriptedModel([[tool_call("send_email", to="someone@example.com")], ["I will ask."]])
    activity = ActivityLog(tmp_path / "activity.jsonl")
    approvals = Approvals(tmp_path / "approvals.json")

    _ = [chunk async for chunk in loop.run(model, [send], activity, approvals, [], "Email them")]

    assert ran == []
    assert tool_result(model).startswith("Waiting for the operator's approval")


async def test_every_step_is_recorded_in_the_activity_log(tmp_path: Path, workspace: Path) -> None:
    model = ScriptedModel([[tool_call("write", path="a.md", content="x")], ["Saved."]])

    await run_job(model, workspace, "Save a.md")

    entries = ActivityLog(tmp_path / "activity.jsonl").entries()
    assert [(e["kind"], e.get("name"), e.get("answer")) for e in entries] == [
        ("message", None, None),
        ("tool", "write", "act"),
        ("reply", None, None),
    ]
    assert entries[1]["result"] == "Wrote a.md."


async def test_a_missing_argument_tells_the_model_which_one(workspace: Path) -> None:
    model = ScriptedModel([[tool_call("write", path="a.md")], ["Let me retry."]])

    await run_job(model, workspace, "Save a.md")

    assert tool_result(model) == "Error: write needs the argument 'content'."


async def test_a_tool_that_fails_unexpectedly_is_reported_to_the_model(workspace: Path) -> None:
    model = ScriptedModel([[tool_call("write", path="a.md", content=5)], ["Retrying."]])

    reply = await run_job(model, workspace, "Save a.md")

    assert tool_result(model).startswith("Error: write failed unexpectedly")
    assert reply == "Retrying."


def approved_delete(workspace: Path, path: str) -> str:
    """Ask the agent to delete `path`, approve the request, and return the result."""
    model = ScriptedModel([[tool_call("delete", path=path)], ["I have asked."], ["Noted."]])
    app = create_app(
        model,
        file_tools(workspace),
        ActivityLog(workspace.parent / "activity.jsonl"),
        Approvals(workspace.parent / "approvals.json"),
    )
    client = TestClient(app)
    client.post("/api/chat", json={"message": f"Delete {path}"})
    [request] = client.get("/api/approvals").json()
    return str(client.post(f"/api/approvals/{request['id']}/approve").json()["result"])


def test_deleting_a_folder_is_refused_even_when_approved(workspace: Path) -> None:
    (workspace / "notes").mkdir()
    (workspace / "notes" / "tea.md").write_text("Green")

    result = approved_delete(workspace, "notes")

    assert (workspace / "notes" / "tea.md").exists()
    assert result == "Error: notes is a folder; only single files can be deleted."


async def test_renaming_moves_a_file(workspace: Path) -> None:
    (workspace / "draft.md").write_text("Plan")
    model = ScriptedModel([[tool_call("rename", path="draft.md", to="plans/final.md")], ["Moved."]])

    await run_job(model, workspace, "Move the draft")

    assert not (workspace / "draft.md").exists()
    assert (workspace / "plans" / "final.md").read_text() == "Plan"


async def test_renaming_never_overwrites_a_file(workspace: Path) -> None:
    (workspace / "a.md").write_text("A")
    (workspace / "b.md").write_text("B")
    model = ScriptedModel([[tool_call("rename", path="a.md", to="b.md")], ["Hm."]])

    await run_job(model, workspace, "Rename a to b")

    assert (workspace / "a.md").read_text() == "A"
    assert (workspace / "b.md").read_text() == "B"
    assert tool_result(model) == "Error: b.md already exists; rename will not overwrite it."


def test_deleting_a_link_is_refused_and_the_real_file_is_kept(workspace: Path) -> None:
    (workspace / "real.md").write_text("keep me")
    (workspace / "link.md").symlink_to(workspace / "real.md")

    result = approved_delete(workspace, "link.md")

    assert (workspace / "real.md").read_text() == "keep me"
    assert result == "Error: link.md is a link; links cannot be deleted or renamed."
