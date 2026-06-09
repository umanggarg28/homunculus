"""Exit the agent loop as soon as the declared due tasks are closed.

Live waste 2026-06-09 trace: a heartbeat tick delivered the LeetCode
problem at iter 4 (notify → complete_task succeeded). With
tool_choice=required, the loop kept spinning — list_tasks at iter 5,
get_current_time at iter 9, two required_tool_violation retries in
between, finally bailing at max_turns with prose "The task has been
completed." Five wasted LLM calls + two detector retries per success.

Fix: when the caller declares `expected_completions=N`, count successful
calls to terminal task tools (complete_task / continue_task /
cancel_task / record_failure). When N closes are recorded, exit
cleanly with a "✓ Done." reply.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import core
import tools as tools_module


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
def stubbed_tools(monkeypatch):
    schemas = [_schema(n) for n in (
        "notify", "complete_task", "continue_task",
        "cancel_task", "record_failure", "list_tasks",
    )]
    monkeypatch.setattr(tools_module, "SCHEMAS", schemas, raising=False)
    dispatched: list[tuple[str, dict]] = []

    def fake_execute(name: str, args: dict) -> str:
        dispatched.append((name, args))
        return "OK"

    monkeypatch.setattr(tools_module, "execute", fake_execute, raising=False)
    return dispatched


def _tool_call(name: str, cid: str = "c1", args: str = "{}") -> dict:
    return {
        "id": cid,
        "type": "function",
        "function": {"name": name, "arguments": args},
    }


def _assistant_msg(*tool_call_names: str) -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [_tool_call(n, cid=f"c{i}") for i, n in enumerate(tool_call_names)],
    }


# ---- the core promise -------------------------------------------------


def test_loop_exits_after_one_complete_task_when_one_expected(stubbed_tools):
    """Single due task → one complete_task → loop exits, no further LLM
    calls. This is the user-facing fix: the wasted 5+ turns disappear."""
    agent = core.Agent(memory=None)
    call_count = {"n": 0}

    def fake(messages, tool_schemas, model=None, tool_choice="auto",
             reasoning_effort="low", provider_constraints=None):
        call_count["n"] += 1
        # First call: emit the completion. The loop must exit before
        # any second LLM call would happen.
        return _assistant_msg("complete_task")

    with patch.object(core, "call_llm", side_effect=fake):
        out = "".join(agent._run_loop(
            "tick", streaming=False, source="heartbeat",
            expected_completions=1,
        ))

    assert call_count["n"] == 1, (
        f"expected exactly 1 LLM call (the one producing complete_task), "
        f"got {call_count['n']}"
    )
    assert "Done" in out or "done" in out, (
        f"expected a clean done-reply on exit, got: {out!r}"
    )


def test_loop_keeps_running_until_all_expected_completions(stubbed_tools):
    """Two due tasks → loop waits for two terminal calls before exiting."""
    agent = core.Agent(memory=None)
    call_count = {"n": 0}
    msgs = iter([
        # Turn 1: closes task A.
        _assistant_msg("complete_task"),
        # Turn 2: closes task B → exit after this turn.
        _assistant_msg("complete_task"),
    ])

    def fake(messages, tool_schemas, model=None, tool_choice="auto",
             reasoning_effort="low", provider_constraints=None):
        call_count["n"] += 1
        return next(msgs)

    with patch.object(core, "call_llm", side_effect=fake):
        list(agent._run_loop(
            "tick", streaming=False, source="heartbeat",
            expected_completions=2,
        ))

    assert call_count["n"] == 2, (
        f"expected 2 LLM calls (one per task close), got {call_count['n']}"
    )


def test_continue_task_counts_as_completion(stubbed_tools):
    """continue_task is also a terminal call — the agent has yielded
    responsibility for this tick. Loop should exit."""
    agent = core.Agent(memory=None)
    call_count = {"n": 0}

    def fake(messages, tool_schemas, model=None, tool_choice="auto",
             reasoning_effort="low", provider_constraints=None):
        call_count["n"] += 1
        return _assistant_msg("continue_task")

    with patch.object(core, "call_llm", side_effect=fake):
        list(agent._run_loop(
            "tick", streaming=False, source="heartbeat",
            expected_completions=1,
        ))

    assert call_count["n"] == 1


def test_cancel_task_counts_as_completion(stubbed_tools):
    agent = core.Agent(memory=None)
    call_count = {"n": 0}

    def fake(messages, tool_schemas, model=None, tool_choice="auto",
             reasoning_effort="low", provider_constraints=None):
        call_count["n"] += 1
        return _assistant_msg("cancel_task")

    with patch.object(core, "call_llm", side_effect=fake):
        list(agent._run_loop(
            "tick", streaming=False, source="heartbeat",
            expected_completions=1,
        ))

    assert call_count["n"] == 1


def test_record_failure_counts_as_completion(stubbed_tools):
    agent = core.Agent(memory=None)
    call_count = {"n": 0}

    def fake(messages, tool_schemas, model=None, tool_choice="auto",
             reasoning_effort="low", provider_constraints=None):
        call_count["n"] += 1
        return _assistant_msg("record_failure")

    with patch.object(core, "call_llm", side_effect=fake):
        list(agent._run_loop(
            "tick", streaming=False, source="heartbeat",
            expected_completions=1,
        ))

    assert call_count["n"] == 1


def test_non_terminal_tools_do_not_trigger_exit(stubbed_tools):
    """A successful notify() is NOT terminal — the agent still has to
    call complete_task. If we exited on notify the loop would close
    without recording the close."""
    agent = core.Agent(memory=None)
    call_count = {"n": 0}
    msgs = iter([
        _assistant_msg("notify"),         # not terminal
        _assistant_msg("complete_task"),  # terminal → exit
    ])

    def fake(messages, tool_schemas, model=None, tool_choice="auto",
             reasoning_effort="low", provider_constraints=None):
        call_count["n"] += 1
        return next(msgs)

    with patch.object(core, "call_llm", side_effect=fake):
        list(agent._run_loop(
            "tick", streaming=False, source="heartbeat",
            expected_completions=1,
        ))

    assert call_count["n"] == 2, (
        f"notify shouldn't exit; loop must continue until complete_task. "
        f"got {call_count['n']} LLM calls"
    )


def test_default_no_expected_completions_keeps_legacy_behavior(stubbed_tools):
    """Callers that don't pass expected_completions (chat path, tests
    written before this fix) must see no behavior change."""
    agent = core.Agent(memory=None)
    call_count = {"n": 0}

    def fake(messages, tool_schemas, model=None, tool_choice="auto",
             reasoning_effort="low", provider_constraints=None):
        call_count["n"] += 1
        return _assistant_msg("complete_task")

    with patch.object(core, "call_llm", side_effect=fake):
        # No expected_completions — loop should NOT exit on complete_task.
        list(agent._run_loop(
            "tick", streaming=False, source="heartbeat",
            # expected_completions=None  (default)
        ))

    # The loop will run to max_turns; way more than 1.
    assert call_count["n"] > 1, (
        f"with no expected_completions, loop should not exit early; "
        f"got {call_count['n']} LLM calls (expected to hit max_turns)"
    )


# ---- failed terminal call should NOT count ---------------------------


def test_failed_complete_task_does_not_decrement_count(stubbed_tools, monkeypatch):
    """If complete_task returns an ERROR (e.g., success_criteria blocked
    the close), the terminal call did NOT succeed — the counter must
    not increment. Otherwise we'd exit on a task that never delivered."""
    call_idx = {"complete_task": 0}

    def fake_execute(name: str, args: dict) -> str:
        # First complete_task ERRORs (criteria blocked); second succeeds.
        if name == "complete_task":
            call_idx["complete_task"] += 1
            if call_idx["complete_task"] == 1:
                return "ERROR: success_criteria not met (notify_called)"
            return "OK"
        return "OK"

    monkeypatch.setattr(tools_module, "execute", fake_execute, raising=False)

    agent = core.Agent(memory=None)
    call_count = {"n": 0}
    msgs = iter([
        _assistant_msg("complete_task"),  # fails — must not count
        _assistant_msg("notify"),         # corrective action
        _assistant_msg("complete_task"),  # succeeds → exit
    ])

    def fake(messages, tool_schemas, model=None, tool_choice="auto",
             reasoning_effort="low", provider_constraints=None):
        call_count["n"] += 1
        return next(msgs)

    with patch.object(core, "call_llm", side_effect=fake):
        list(agent._run_loop(
            "tick", streaming=False, source="heartbeat",
            expected_completions=1,
        ))

    assert call_count["n"] == 3, (
        f"first complete_task ERRORed → must not count; loop must run "
        f"until the second succeeds. got {call_count['n']} LLM calls"
    )


# ---- heartbeat wiring -------------------------------------------------


def test_heartbeat_tick_passes_expected_completions():
    """Static check: heartbeat.tick passes expected_completions=len(due_tasks)
    so the exit branch is reachable in production."""
    from pathlib import Path
    src = (Path(__file__).parent.parent / "heartbeat.py").read_text()
    # Find the agent.chat( call inside tick(). The non-comment call
    # must include expected_completions=.
    assert "expected_completions=len(due_tasks)" in src or (
        "expected_completions=" in src and "len(due_tasks)" in src
    ), (
        "heartbeat.tick must pass expected_completions=len(due_tasks) "
        "to agent.chat — otherwise the loop-exit branch never fires in "
        "production."
    )
