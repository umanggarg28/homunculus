import pytest

from homunculus.gate import Answer, decide
from homunculus.model import ToolCall


def call(name: str, arguments: str) -> ToolCall:
    return ToolCall(id="call_1", name=name, arguments=arguments)


@pytest.mark.parametrize("tool", ["read", "write", "edit", "list", "search"])
def test_workspace_file_actions_are_allowed(tool: str) -> None:
    assert decide(call(tool, '{"path": "notes.md"}')) is Answer.ACT


def test_an_action_not_in_the_table_asks_first() -> None:
    assert decide(call("send_email", '{"to": "someone@example.com"}')) is Answer.ASK


def test_a_call_the_gate_cannot_read_is_refused() -> None:
    assert decide(call("write", "{not json")) is Answer.REFUSE


def test_a_file_action_without_a_path_is_refused() -> None:
    assert decide(call("write", '{"content": "hello"}')) is Answer.REFUSE
