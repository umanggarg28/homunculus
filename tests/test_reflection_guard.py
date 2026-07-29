"""The reflection tick enforces its own rules at the harness level.

Regression: a daily REFLECTION tick fired a false "Updated skill_hn_ai_summary
to use Algolia API" notification to the user (2026-06-26). The reflection prompt
says "Rules: no notify()", but reflection installed no pre-execute hook, so the
weak model called notify() anyway — and the message was a fabricated claim (no
skill write happened that tick). This guard makes the rule real.
"""

from __future__ import annotations

from homunculus import heartbeat


def test_blocks_the_exact_false_ping_from_the_trace():
    # The literal call from the 2026-06-26 reflection trace.
    refusal = heartbeat._reflection_tool_guard(
        "notify",
        {"text": "Updated skill_hn_ai_summary to use Algolia API for reliable links."},
    )
    assert refusal is not None
    assert refusal.startswith("BLOCKED")
    assert "don't message the user" in refusal


def test_blocks_task_lifecycle_calls():
    # Reflection also wrongly called continue_task/complete_task on leetcode.
    for name in ("complete_task", "continue_task", "record_failure"):
        assert heartbeat._reflection_tool_guard(name, {}) is not None, name


def test_blocks_workspace_writes_and_shell():
    for name in ("write_file", "append_file", "shell_exec"):
        assert heartbeat._reflection_tool_guard(name, {}) is not None, name


def test_blocks_create_task_status_notes():
    """Live misuse (07-14→22): reflection minted junk tasks as status
    notes — 'reflection-completed-2026-07-15' (active forever, due=None),
    'all-skill-deliveries-succeeded-…'. Reflection records real
    commitments via record_commitment; create_task there is always junk."""
    refusal = heartbeat._reflection_tool_guard(
        "create_task", {"title": "reflection completed 2026-07-15"},
    )
    assert refusal is not None and "record_commitment" in refusal


def test_allows_the_tools_reflection_actually_needs():
    # Skill review + memory hygiene must pass straight through —
    # including record_commitment, the sanctioned follow-up mechanism.
    for name in ("read_file", "recall", "list_proposals", "propose_skill",
                 "remember", "forget", "record_commitment"):
        assert heartbeat._reflection_tool_guard(name, {}) is None, name


def test_refusal_is_returned_as_a_tool_result_string():
    # The pre-execute hook contract: a non-empty string becomes the tool result
    # the agent sees, so it self-corrects instead of silently messaging the user.
    refusal = heartbeat._reflection_tool_guard("notify", {"text": "hi"})
    assert isinstance(refusal, str) and refusal
