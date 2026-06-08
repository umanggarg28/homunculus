"""Heartbeat ticks force tool_choice='required'; chat paths stay 'auto'.

Letta's `function_call: "required"` pattern (letta/llm_api/llm_api_tools.py
line 200) — every turn must end in a tool call. Removes the failure mode
where the model drafts a delivery (LeetCode solution, morning brief)
as free-form assistant_reply text the user never sees. Heartbeat is the
right path because no user is watching the streamed text in real time.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import core


def _capture_tool_choice():
    """Return a (calls, fake_call_llm) pair that records every tool_choice
    passed and short-circuits the loop with a complete_task to exit cleanly."""
    calls: list[str] = []

    def fake(messages, tool_schemas, model=None, tool_choice="auto", reasoning_effort="low", provider_constraints=None):
        calls.append(tool_choice)
        # Return a synthetic complete_task call so the loop exits cleanly
        # on its first iteration. This sidesteps real API I/O.
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "c1",
                "type": "function",
                "function": {
                    "name": "complete_task",
                    "arguments": '{"task_id": "x", "result": "done"}',
                },
            }],
        }

    return calls, fake


def test_chat_source_uses_auto_tool_choice():
    """Web/telegram/repl users see streaming text — keep auto so the
    model is free to produce a chat reply as the final turn."""
    agent = core.Agent(memory=None)
    calls, fake = _capture_tool_choice()
    with patch.object(core, "call_llm", side_effect=fake):
        list(agent._run_loop("hi", streaming=False, source="web"))
    assert calls, "expected call_llm to be invoked at least once"
    assert all(tc == "auto" for tc in calls), (
        f"chat path must use auto tool_choice, got: {calls}"
    )


def test_heartbeat_source_uses_required_tool_choice():
    """The autonomous tick has no user watching free-form text — every
    effect must go through a tool call (notify, complete_task, etc.).
    Forcing 'required' removes the 'wrote content into assistant_reply'
    failure mode that's been killing morning deliveries."""
    agent = core.Agent(memory=None)
    calls, fake = _capture_tool_choice()
    with patch.object(core, "call_llm", side_effect=fake):
        list(agent._run_loop("tick prompt", streaming=False, source="heartbeat"))
    assert calls, "expected call_llm to be invoked at least once"
    assert all(tc == "required" for tc in calls), (
        f"heartbeat path must force required tool_choice, got: {calls}"
    )


def test_call_llm_default_is_auto():
    """Existing callers that haven't been migrated must keep the
    permissive default — this is a non-breaking parameter add."""
    import inspect
    sig = inspect.signature(core.call_llm)
    assert sig.parameters["tool_choice"].default == "auto"


def test_call_llm_stream_default_is_auto():
    import inspect
    sig = inspect.signature(core.call_llm_stream)
    assert sig.parameters["tool_choice"].default == "auto"


def test_refinement_source_uses_required_tool_choice_and_medium_reasoning():
    """Skill refinement mode: model MUST call a tool every turn (can't
    drift into prose like 'I'll start the refinement') AND gets medium
    reasoning effort to actually think through API discovery."""
    agent = core.Agent(memory=None)
    tool_choices: list[str] = []
    reasoning_efforts: list[str] = []

    def fake(messages, tool_schemas, model=None, tool_choice="auto", reasoning_effort="low", provider_constraints=None):
        tool_choices.append(tool_choice)
        reasoning_efforts.append(reasoning_effort)
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "c1",
                "type": "function",
                "function": {
                    "name": "complete_task",
                    "arguments": '{"task_id": "x", "result": "done"}',
                },
            }],
        }

    from unittest.mock import patch
    with patch.object(core, "call_llm", side_effect=fake):
        list(agent._run_loop("kick", streaming=False, source="refinement"))

    assert tool_choices, "expected call_llm to fire"
    assert all(t == "required" for t in tool_choices), (
        f"refinement must force tool_choice=required, got: {tool_choices}"
    )
    assert all(r == "medium" for r in reasoning_efforts), (
        f"refinement must use medium reasoning effort, got: {reasoning_efforts}"
    )


def test_chat_uses_low_reasoning_effort_default():
    agent = core.Agent(memory=None)
    efforts: list[str] = []

    def fake(messages, tool_schemas, model=None, tool_choice="auto", reasoning_effort="low", provider_constraints=None):
        efforts.append(reasoning_effort)
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "c1",
                "type": "function",
                "function": {
                    "name": "complete_task",
                    "arguments": '{"task_id": "x", "result": "done"}',
                },
            }],
        }

    from unittest.mock import patch
    with patch.object(core, "call_llm", side_effect=fake):
        list(agent._run_loop("hi", streaming=False, source="web"))

    assert efforts and all(e == "low" for e in efforts)
