"""Output-guard rule: false forward-tense MUTATION promises.

Same dishonesty family as action_claim_without_tool_call (past tense) and
false_future_promise (retry shape). The live case that motivated it: the user
asked to add explanations to the daily-LeetCode skill and the agent replied
"I'll edit the daily-LeetCode skill to add a concise explanation…" — with ZERO
tool calls. The turn ends, the edit never happens. The guard forces the model
to actually call the mutating tool (propose_skill / create_task / …) instead.

The rule only fires when tools are available, the reply isn't a clarifying
question, and no mutating tool ran this turn.
"""

from __future__ import annotations

import sys
import types

import pytest

if "homunculus.tools.notify" not in sys.modules:
    _stub = types.ModuleType("homunculus.tools.notify")
    _stub._send_to_telegram = lambda *_a, **_kw: None
    sys.modules["homunculus.tools.notify"] = _stub

from homunculus import core  # noqa: E402


@pytest.fixture
def tools_available(monkeypatch):
    """The guard is gated on tools being registered (not a Q&A-only session)."""
    monkeypatch.setattr(core.tools, "SCHEMAS", [{"name": "propose_skill"}], raising=False)


def test_leetcode_edit_promise_without_tool_is_flagged(tools_available):
    agent = core.Agent()
    reply = (
        "I'll edit the daily-LeetCode skill to add a concise explanation of the "
        "solution, including time and space complexity."
    )
    clean, violations = agent._output_guard(reply, set(), [])
    assert clean is None
    assert "false_mutation_promise" in violations


def test_create_task_promise_without_tool_is_flagged(tools_available):
    agent = core.Agent()
    reply = "Sure — I'll create a task to remind you every morning at 9."
    _, violations = agent._output_guard(reply, set(), [])
    assert "false_mutation_promise" in violations


def test_promise_with_mutating_tool_called_passes(tools_available):
    """If the agent actually called propose_skill this turn, the promise is
    being acted on — no violation."""
    agent = core.Agent()
    reply = "I'll edit the skill to add explanations — filing that now."
    clean, violations = agent._output_guard(
        reply, {"propose_skill"}, [{"name": "propose_skill", "success": True}]
    )
    assert "false_mutation_promise" not in violations


def test_clarifying_question_is_not_flagged(tools_available):
    """A promise paired with a clarifying question is the agent engaging, not
    a dead-end promise — don't block it."""
    agent = core.Agent()
    reply = "Happy to update the skill — should it also cover edge cases?"
    _, violations = agent._output_guard(reply, set(), [])
    assert "false_mutation_promise" not in violations


def test_no_concrete_artifact_is_not_flagged(tools_available):
    """'I'll update you' has a promise verb but no artifact noun — not a
    state mutation, must not trip the guard."""
    agent = core.Agent()
    reply = "Got it. I'll update you as soon as the run finishes."
    _, violations = agent._output_guard(reply, set(), [])
    assert "false_mutation_promise" not in violations


def test_qa_only_session_does_not_fire(monkeypatch):
    """With no tools registered (pure Q&A), the guard must not fire — the model
    has no tool to call."""
    monkeypatch.setattr(core.tools, "SCHEMAS", [], raising=False)
    agent = core.Agent()
    reply = "I'll edit the skill to add explanations."
    _, violations = agent._output_guard(reply, set(), [])
    assert "false_mutation_promise" not in violations


def test_past_tense_completion_not_caught_here(tools_available):
    """Past-tense false claims are a different rule's job; this regex is
    forward-tense only, so it shouldn't match 'I edited the skill'."""
    agent = core.Agent()
    reply = "I edited the skill to add explanations."
    _, violations = agent._output_guard(reply, set(), [])
    assert "false_mutation_promise" not in violations
