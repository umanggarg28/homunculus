"""The permission gate as the loop actually applies it.

`test_permissions.py` pins the policy's decisions in isolation. This file
pins the wiring: that a denied call never reaches `tools.execute`, that its
reason comes back as the tool result the model reads, and that corrected
arguments are what the tool is actually run with.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from homunculus import core
from homunculus.permissions import PermissionPolicy, PermissionRule
import homunculus.tools as tools_module


def _schema(name: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": "test stub",
            "parameters": {"type": "object", "properties": {}},
        },
    }


@pytest.fixture
def dispatched(monkeypatch):
    """Record what actually reaches the tool layer."""
    schemas = [_schema(n) for n in ("read_file", "write_file", "get_weather", "notify")]
    monkeypatch.setattr(tools_module, "SCHEMAS", schemas, raising=False)
    calls: list[tuple[str, dict]] = []

    def fake_execute(name: str, args: dict) -> str:
        calls.append((name, args))
        return "OK"

    monkeypatch.setattr(tools_module, "execute", fake_execute, raising=False)
    return calls


def _dispatch(agent: core.Agent, name: str, arguments: str = "{}") -> list[dict]:
    """Run one tool call through the dispatch phase; return the outcomes."""
    outcomes: list[dict] = []
    agent._dispatch_tool_calls(
        [{"id": "c0", "type": "function",
          "function": {"name": name, "arguments": arguments}}],
        set(), {}, {}, outcomes,
    )
    return outcomes


def test_denied_tool_never_executes(dispatched):
    agent = core.Agent(
        memory=None,
        permissions=PermissionPolicy(
            rules=(PermissionRule("write_file", "deny", "this run is read-only"),)
        ),
    )
    outcomes = _dispatch(agent, "write_file", '{"path": "notes.md"}')

    assert dispatched == [], "denied call reached the tool layer"
    assert len(outcomes) == 1
    assert "write_file" in outcomes[0]["result"]
    assert "read-only" in outcomes[0]["result"]


def test_allowed_tool_still_executes(dispatched):
    agent = core.Agent(
        memory=None,
        permissions=PermissionPolicy(
            rules=(PermissionRule("write_file", "deny"),)
        ),
    )
    _dispatch(agent, "read_file", '{"path": "notes.md"}')

    assert dispatched == [("read_file", {"path": "notes.md"})]


def test_readonly_mode_blocks_mutation_end_to_end(dispatched):
    agent = core.Agent(memory=None, permissions=PermissionPolicy(mode="readonly"))
    _dispatch(agent, "write_file", '{"path": "notes.md", "content": "x"}')

    assert dispatched == []


def test_corrected_arguments_are_what_the_tool_receives(dispatched):
    """The repair reaches the tool — not just the trace."""
    agent = core.Agent(memory=None, permissions=PermissionPolicy())
    _dispatch(agent, "get_weather", '{"city": "Bangalore<|channel|>commentary"}')

    assert dispatched == [("get_weather", {"city": "Bangalore"})]


def test_corrected_arguments_are_recorded_for_the_output_guard(dispatched):
    agent = core.Agent(memory=None, permissions=PermissionPolicy())
    outcomes = _dispatch(agent, "get_weather", '{"city": "Pune<|end|>"}')

    assert outcomes[0]["args"] == {"city": "Pune"}


def test_leaked_tool_name_still_dispatches(dispatched):
    """Regression: name repair used to be inline in the loop; it moved into
    the policy module and must keep working from there."""
    agent = core.Agent(memory=None, permissions=PermissionPolicy())
    _dispatch(agent, "read_file<|channel|>commentary", '{"path": "a.md"}')

    assert dispatched == [("read_file", {"path": "a.md"})]


def test_denial_does_not_stop_the_rest_of_the_batch(dispatched):
    """One blocked call must not swallow the calls beside it."""
    agent = core.Agent(
        memory=None,
        permissions=PermissionPolicy(rules=(PermissionRule("write_file", "deny"),)),
    )
    outcomes: list[dict] = []
    agent._dispatch_tool_calls(
        [
            {"id": "c0", "type": "function",
             "function": {"name": "write_file", "arguments": '{"path": "a"}'}},
            {"id": "c1", "type": "function",
             "function": {"name": "read_file", "arguments": '{"path": "b"}'}},
        ],
        set(), {}, {}, outcomes,
    )

    assert dispatched == [("read_file", {"path": "b"})]
    assert len(outcomes) == 2


def test_gate_runs_before_the_run_scoped_hook(dispatched):
    """A denied call is settled by the policy; the TaskGuard-style hook is
    never consulted for a call that was not permitted in the first place."""
    seen: list[str] = []

    agent = core.Agent(
        memory=None,
        pre_execute_hook=lambda name, args: seen.append(name) or None,
        permissions=PermissionPolicy(rules=(PermissionRule("write_file", "deny"),)),
    )
    _dispatch(agent, "write_file", "{}")

    assert seen == [], "run-scoped hook consulted for a denied call"


def test_default_agent_permits_normal_work(dispatched):
    """Nothing changes for a caller that never mentions permissions."""
    with patch.object(core, "call_llm"):
        agent = core.Agent(memory=None)
    _dispatch(agent, "notify", '{"message": "hi"}')

    assert dispatched == [("notify", {"message": "hi"})]
