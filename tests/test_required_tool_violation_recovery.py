"""Defense-in-depth: detect & recover from required-tool-choice violations.

After PR #124 pinned OpenRouter to providers that honor `require_parameters`,
the vast majority of refinement / heartbeat turns end in a real tool call.
But OpenRouter's routing isn't guaranteed — a future provider drop, a
routing change, or a sudden compliance regression upstream would silently
re-introduce the text-only-response bug we saw after PR #123.

This detector catches that case at the agent loop:
  - If `tool_choice == "required"` AND response has no tool_calls
  - Inject a synthetic user message demanding a tool call
  - Retry once (capped at 2 retries per run, then fall through)

Emits `required_tool_violation` event for dashboard visibility so a
regression shows up as a measurable rate, not a mystery silent drop.
"""

from __future__ import annotations

from unittest.mock import patch

import core


def _tool_call_msg(name: str = "complete_task") -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": "c1",
            "type": "function",
            "function": {
                "name": name,
                "arguments": '{"task_id": "x", "result": "done"}',
            },
        }],
    }


def _text_only_msg(text: str = "I will start by exploring...") -> dict:
    """Simulates a non-compliant provider returning prose despite
    tool_choice=required."""
    return {"role": "assistant", "content": text, "tool_calls": None}


def _sequence_fake(responses: list[dict]):
    """Build a fake call_llm that returns the next response from the list
    each call. Final response loops forever (defensive — keeps the loop
    moving past the test's intended payload to avoid bogus assertions
    when the loop's exit path is multi-turn)."""
    call_count = {"n": 0}

    def fake(messages, tool_schemas, model=None, tool_choice="auto",
             reasoning_effort="low", provider_constraints=None):
        i = call_count["n"]
        call_count["n"] += 1
        return responses[i] if i < len(responses) else responses[-1]

    return call_count, fake


def test_violation_triggers_correction_message_in_history() -> None:
    """First turn returns prose despite required → detector injects a
    correction → loop continues. The injected correction must be
    visible in self.history (the detector's user-visible contract)."""
    agent = core.Agent(memory=None)
    call_count, fake = _sequence_fake([
        _text_only_msg(),
        _tool_call_msg(),  # second turn: real tool call after correction
    ])

    with patch.object(core, "call_llm", side_effect=fake):
        list(agent._run_loop("kick", streaming=False, source="heartbeat"))

    # The detector must have injected exactly one correction.
    correction_msgs = [
        m for m in agent.history
        if m.get("role") == "user"
        and "did not include a tool call" in (m.get("content") or "")
    ]
    assert len(correction_msgs) == 1, (
        f"expected one synthetic correction in history, got {len(correction_msgs)}"
    )
    # And the LLM was re-invoked (at least twice).
    assert call_count["n"] >= 2


def test_violation_cap_at_two_retries() -> None:
    """Three text-only responses in a row — detector retries twice then
    falls through to the existing text-reply path. History should show
    exactly 2 corrections (the cap)."""
    agent = core.Agent(memory=None)
    call_count, fake = _sequence_fake([
        _text_only_msg("attempt 1"),
        _text_only_msg("attempt 2"),
        _text_only_msg("attempt 3"),
        _tool_call_msg(),  # only reached if cap fails
    ])

    with patch.object(core, "call_llm", side_effect=fake):
        list(agent._run_loop("kick", streaming=False, source="heartbeat"))

    correction_msgs = [
        m for m in agent.history
        if m.get("role") == "user"
        and "did not include a tool call" in (m.get("content") or "")
    ]
    assert len(correction_msgs) == 2, (
        f"detector should cap at 2 retries, got {len(correction_msgs)}"
    )


def test_chat_path_does_not_trigger_detector() -> None:
    """Chat uses tool_choice='auto'. The detector must NOT fire — a
    text-only reply is a valid chat outcome, not a violation."""
    agent = core.Agent(memory=None)
    call_count, fake = _sequence_fake([_text_only_msg("Here's your answer.")])

    with patch.object(core, "call_llm", side_effect=fake):
        list(agent._run_loop("hi", streaming=False, source="web"))

    # No correction message injected.
    correction_msgs = [
        m for m in agent.history
        if m.get("role") == "user"
        and "did not include a tool call" in (m.get("content") or "")
    ]
    assert correction_msgs == [], (
        "chat path must not inject required-tool-call corrections"
    )
