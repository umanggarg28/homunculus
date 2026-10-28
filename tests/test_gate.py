import json
from pathlib import Path

import mcp_types as mcp
import pytest

from homunculus.gate import Answer, decide
from homunculus.model import ToolCall
from homunculus.tools import Tool
from homunculus.tools.files import file_tools


def call(name: str, arguments: str) -> ToolCall:
    return ToolCall(id="call_1", name=name, arguments=arguments)


@pytest.fixture
def tools(tmp_path: Path) -> dict[str, Tool]:
    return {tool.name: tool for tool in file_tools(tmp_path)}


def stand_in(name: str, annotations: mcp.ToolAnnotations | None = None) -> Tool:
    definition = mcp.Tool(
        name=name, description=f"Stand-in {name}.", input_schema={"type": "object"}
    )
    return Tool(definition.model_copy(update={"annotations": annotations}), lambda _: "Done.")


@pytest.mark.parametrize("name", ["read", "list", "search"])
def test_a_read_only_action_is_allowed(name: str, tools: dict[str, Tool]) -> None:
    assert decide(call(name, '{"path": "notes.md"}'), tools[name]) is Answer.ACT


@pytest.mark.parametrize("name", ["write", "edit"])
def test_writing_and_editing_in_the_workspace_are_allowed(
    name: str, tools: dict[str, Tool]
) -> None:
    assert decide(call(name, '{"path": "notes.md"}'), tools[name]) is Answer.ACT


def test_deleting_a_file_asks_first(tools: dict[str, Tool]) -> None:
    assert decide(call("delete", '{"path": "notes.md"}'), tools["delete"]) is Answer.ASK


def test_renaming_inside_the_workspace_is_allowed(tools: dict[str, Tool]) -> None:
    rename = call("rename", '{"path": "a.md", "to": "b/a.md"}')
    assert decide(rename, tools["rename"]) is Answer.ACT


def test_a_tool_that_says_nothing_about_itself_asks_first() -> None:
    # MCP's defaults for a missing hint: not read-only, destructive, and outward.
    assert decide(call("send_email", "{}"), stand_in("send_email")) is Answer.ASK


def test_an_action_that_reaches_outside_asks_first() -> None:
    outward = mcp.ToolAnnotations(read_only_hint=False, destructive_hint=False)
    assert decide(call("post", "{}"), stand_in("post", outward)) is Answer.ASK


def test_an_additive_action_inside_the_program_is_allowed() -> None:
    closed = mcp.ToolAnnotations(destructive_hint=False, open_world_hint=False)
    assert decide(call("add_note", "{}"), stand_in("add_note", closed)) is Answer.ACT


def test_a_destructive_action_inside_the_program_asks_first() -> None:
    closed = mcp.ToolAnnotations(destructive_hint=True, open_world_hint=False)
    assert decide(call("clear_notes", "{}"), stand_in("clear_notes", closed)) is Answer.ASK


def test_a_tool_that_does_not_exist_is_refused() -> None:
    assert decide(call("launch", "{}"), None) is Answer.REFUSE


def test_a_call_the_gate_cannot_read_is_refused(tools: dict[str, Tool]) -> None:
    assert decide(call("write", "{not json"), tools["write"]) is Answer.REFUSE


def test_a_file_action_without_a_path_is_refused(tools: dict[str, Tool]) -> None:
    assert decide(call("write", '{"content": "hello"}'), tools["write"]) is Answer.REFUSE


@pytest.mark.parametrize("path", ["/etc/passwd", "../outside.md", "notes/../../outside.md"])
def test_a_file_action_aimed_outside_the_workspace_is_refused(
    path: str, tools: dict[str, Tool]
) -> None:
    write = call("write", json.dumps({"path": path, "content": "x"}))
    assert decide(write, tools["write"]) is Answer.REFUSE


def test_arguments_that_are_not_an_object_are_refused(tools: dict[str, Tool]) -> None:
    assert decide(call("read", "[]"), tools["read"]) is Answer.REFUSE


@pytest.mark.parametrize("to", ["/tmp/a.md", "../a.md"])
def test_a_rename_aimed_outside_the_workspace_is_refused(to: str, tools: dict[str, Tool]) -> None:
    rename = call("rename", json.dumps({"path": "a.md", "to": to}))
    assert decide(rename, tools["rename"]) is Answer.REFUSE


@pytest.mark.parametrize("name", ["pay", "enter_password", "enter_verification_code"])
def test_payments_passwords_and_codes_are_handed_to_the_operator(name: str) -> None:
    assert decide(call(name, "{}"), stand_in(name)) is Answer.HAND_OFF


def test_a_hand_off_the_gate_cannot_read_is_refused() -> None:
    assert decide(call("pay", "{not json"), stand_in("pay")) is Answer.REFUSE


def test_a_tool_that_runs_in_the_sandbox_is_allowed() -> None:
    # Honestly destructive and outward, yet the sandbox is its boundary.
    outward = mcp.ToolAnnotations(destructive_hint=True, open_world_hint=True)
    sandboxed = stand_in("shell", outward)
    sandboxed = Tool(sandboxed.definition, sandboxed.run, runs_in_sandbox=True)
    assert decide(call("shell", '{"command": "ls"}'), sandboxed) is Answer.ACT
