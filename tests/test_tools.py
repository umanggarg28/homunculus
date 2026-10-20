"""File tools, exercised through the loop with a scripted model."""

import json
from pathlib import Path

import pytest
from scripted_model import ScriptedModel

from homunculus import loop
from homunculus.activity import ActivityLog
from homunculus.model import Message, ToolCall
from homunculus.tools import Tool, file_tools


def tool_call(name: str, **arguments: object) -> ToolCall:
    return ToolCall(id=f"call_{name}", name=name, arguments=json.dumps(arguments))


async def run_job(model: ScriptedModel, workspace: Path, text: str) -> str:
    activity = ActivityLog(workspace.parent / "activity.jsonl")
    history: list[Message] = []
    chunks = [
        chunk async for chunk in loop.run(model, file_tools(workspace), activity, history, text)
    ]
    return "".join(chunks)


def tool_result(model: ScriptedModel, call: int = 1) -> str:
    """The tool result the model was shown at the start of its `call`-th turn."""
    last = model.received[call][-1]
    assert last["role"] == "tool"
    return str(last["content"])


async def test_the_agent_can_write_a_file_in_its_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    model = ScriptedModel(
        [[tool_call("write", path="notes/tea.md", content="I like tea.\n")], ["Saved."]]
    )

    reply = await run_job(model, workspace, "Note that I like tea.")

    assert (workspace / "notes" / "tea.md").read_text() == "I like tea.\n"
    assert reply == "Saved."
    assert len(model.received) == 2


async def test_reading_a_file_shows_numbered_lines(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "plan.md").write_text("Buy tea\nCall home\n")
    model = ScriptedModel([[tool_call("read", path="plan.md")], ["Two things."]])

    await run_job(model, workspace, "What is on my plan?")

    assert tool_result(model) == "1\tBuy tea\n2\tCall home"


async def test_reading_a_missing_file_tells_the_model(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    model = ScriptedModel([[tool_call("read", path="nope.md")], ["It does not exist."]])

    await run_job(model, workspace, "Read nope.md")

    assert tool_result(model) == "Error: nope.md does not exist."


@pytest.mark.parametrize("path", ["../outside.md", "/tmp/outside.md", "notes/../../outside.md"])
async def test_a_path_outside_the_workspace_is_refused(tmp_path: Path, path: str) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    model = ScriptedModel([[tool_call("write", path=path, content="escaped")], ["Done."]])

    await run_job(model, workspace, "Write outside")

    assert tool_result(model) == f"Error: {path} is outside the workspace."
    assert not (tmp_path / "outside.md").exists()


async def test_an_allowed_tool_that_does_not_exist_tells_the_model(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    model = ScriptedModel(
        [[ToolCall(id="call_1", name="search", arguments='{"path": "."}')], ["Sorry."]]
    )
    activity = ActivityLog(tmp_path / "activity.jsonl")
    no_tools: list[Tool] = []

    _ = [chunk async for chunk in loop.run(model, no_tools, activity, [], "Search")]

    assert tool_result(model) == "Error: there is no tool named search."


async def test_a_link_that_leads_outside_the_workspace_is_refused(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (tmp_path / "private.md").write_text("secret")
    (workspace / "shortcut.md").symlink_to(tmp_path / "private.md")
    model = ScriptedModel([[tool_call("read", path="shortcut.md")], ["Refused."]])

    await run_job(model, workspace, "Read the shortcut")

    assert tool_result(model) == "Error: shortcut.md is outside the workspace."


async def test_listing_shows_folders_and_files(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "notes").mkdir(parents=True)
    (workspace / "plan.md").write_text("Buy tea\n")
    (workspace / "notes" / "tea.md").write_text("Green\n")
    model = ScriptedModel([[tool_call("list", path=".")], ["Here you go."]])

    await run_job(model, workspace, "What is in my workspace?")

    assert tool_result(model) == "notes/\nplan.md"


async def test_editing_replaces_text_that_appears_once(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "plan.md").write_text("Buy tea\nCall home\n")
    model = ScriptedModel(
        [[tool_call("edit", path="plan.md", old="Buy tea", new="Buy green tea")], ["Changed."]]
    )

    await run_job(model, workspace, "Make it green tea")

    assert (workspace / "plan.md").read_text() == "Buy green tea\nCall home\n"


async def test_editing_text_that_is_not_unique_changes_nothing(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "plan.md").write_text("tea\ntea\n")
    model = ScriptedModel([[tool_call("edit", path="plan.md", old="tea", new="coffee")], ["Hm."]])

    await run_job(model, workspace, "Change tea")

    assert (workspace / "plan.md").read_text() == "tea\ntea\n"
    assert tool_result(model) == "Error: the text appears 2 times in plan.md; it must appear once."


async def test_search_finds_matching_lines_across_files(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "notes").mkdir(parents=True)
    (workspace / "plan.md").write_text("Buy Tea\nCall home\n")
    (workspace / "notes" / "drinks.md").write_text("Coffee\nGreen tea is best\n")
    model = ScriptedModel([[tool_call("search", path=".", text="tea")], ["Found it."]])

    await run_job(model, workspace, "Where do I mention tea?")

    assert tool_result(model) == "notes/drinks.md:2: Green tea is best\nplan.md:1: Buy Tea"


async def test_an_action_that_needs_approval_is_not_run(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ran: list[dict[str, object]] = []

    def send_email(arguments: dict[str, object]) -> str:
        ran.append(arguments)
        return "Sent."

    send = Tool("send_email", "Send an email.", {"type": "object"}, send_email)
    model = ScriptedModel([[tool_call("send_email", to="someone@example.com")], ["I will ask."]])
    activity = ActivityLog(tmp_path / "activity.jsonl")

    _ = [chunk async for chunk in loop.run(model, [send], activity, [], "Email them")]

    assert ran == []
    assert tool_result(model) == "Not done: the gate answered 'ask' for this action."


async def test_every_step_is_recorded_in_the_activity_log(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    model = ScriptedModel([[tool_call("write", path="a.md", content="x")], ["Saved."]])

    await run_job(model, workspace, "Save a.md")

    entries = ActivityLog(tmp_path / "activity.jsonl").entries()
    assert [(e["kind"], e.get("name"), e.get("answer")) for e in entries] == [
        ("message", None, None),
        ("tool", "write", "act"),
        ("reply", None, None),
    ]
    assert entries[1]["result"] == "Wrote a.md."


async def test_a_missing_argument_tells_the_model_which_one(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    model = ScriptedModel([[tool_call("write", path="a.md")], ["Let me retry."]])

    await run_job(model, workspace, "Save a.md")

    assert tool_result(model) == "Error: write needs the argument 'content'."


async def test_a_tool_that_fails_unexpectedly_is_reported_to_the_model(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    model = ScriptedModel([[tool_call("write", path="a.md", content=5)], ["Retrying."]])

    reply = await run_job(model, workspace, "Save a.md")

    assert tool_result(model).startswith("Error: write failed unexpectedly")
    assert reply == "Retrying."
