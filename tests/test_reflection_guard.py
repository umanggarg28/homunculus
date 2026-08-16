"""The reflection tick enforces its own rules at the harness level.

Regression: a daily REFLECTION tick fired a false "Updated skill_hn_ai_summary
to use Algolia API" notification to the user (2026-06-26). The reflection prompt
says "Rules: no notify()", but reflection installed no pre-execute hook, so the
weak model called notify() anyway — and the message was a fabricated claim (no
skill write happened that tick). This guard makes the rule real.
"""

from __future__ import annotations

from homunculus import heartbeat


def _guard():
    return heartbeat._ReflectionToolGuard()


def test_blocks_the_exact_false_ping_from_the_trace():
    # The literal call from the 2026-06-26 reflection trace.
    refusal = _guard()(
        "notify",
        {"text": "Updated skill_hn_ai_summary to use Algolia API for reliable links."},
    )
    assert refusal is not None
    assert refusal.startswith("BLOCKED")
    assert "don't message the user" in refusal


def test_blocks_task_lifecycle_calls():
    # Reflection also wrongly called continue_task/complete_task on leetcode.
    guard = _guard()
    for name in ("complete_task", "continue_task", "record_failure"):
        assert guard(name, {}) is not None, name


def test_blocks_workspace_writes_and_shell():
    guard = _guard()
    for name in ("write_file", "append_file", "shell_exec"):
        assert guard(name, {}) is not None, name


def test_blocks_create_task_status_notes():
    """Live misuse (07-14→22): reflection minted junk tasks as status
    notes — 'reflection-completed-2026-07-15' (active forever, due=None),
    'all-skill-deliveries-succeeded-…'."""
    refusal = _guard()("create_task", {"title": "reflection completed 2026-07-15"})
    assert refusal is not None and "reminder tasks" in refusal


def test_the_create_task_refusal_does_not_advertise_another_door():
    """This refusal used to end "...use record_commitment", which moved the
    misuse instead of stopping it: the model filed the same junk through the
    other door. A refusal that names an alternative is an instruction to use
    it."""
    refusal = _guard()("create_task", {"title": "x"})
    assert refusal is not None and "record_commitment" not in refusal


def test_reflection_cannot_record_commitments():
    """Both commitments reflection ever recorded were misuse: an agent status
    note ("prop-0041 still pending approval") and a behavioural rule
    ("Reminder to not send unsolicited messages") — the latter scheduled to
    arrive as an unsolicited message. A commitment is something the user
    undertook, recorded where it is observed, not inferred a day later."""
    refusal = _guard()("record_commitment", {"what": "prop-0041 still pending approval"})
    assert refusal is not None and refusal.startswith("BLOCKED:")
    assert "observed" in refusal


def test_allows_the_tools_reflection_actually_needs():
    # Skill review + memory hygiene must pass straight through. Scheduling is
    # deliberately not on this list: reflection reviews and proposes, and the
    # harness surfaces what it filed.
    guard = _guard()
    for name in ("read_file", "recall", "list_proposals", "propose_skill",
                 "remember", "forget"):
        assert guard(name, {}) is None, name


def test_refusal_is_returned_as_a_tool_result_string():
    # The pre-execute hook contract: a non-empty string becomes the tool result
    # the agent sees, so it self-corrects instead of silently messaging the user.
    refusal = _guard()("notify", {"text": "hi"})
    assert isinstance(refusal, str) and refusal


def test_remember_capped_at_two_per_tick():
    """Live waste (2026-08-01): one reflection tick called remember() 11
    times, all paraphrases of the same daily summary — the prompt says
    'AT MOST 2' but nothing enforced it, so the model kept rewording past
    the generic STUCK_LOOP guard every time it varied the text. The cap
    makes the limit real regardless of phrasing."""
    guard = _guard()
    assert guard("remember", {"body": "first take"}) is None
    assert guard("remember", {"body": "second take, reworded"}) is None
    refusal = guard("remember", {"body": "third take, reworded again"})
    assert refusal is not None
    assert refusal.startswith("BLOCKED") and "remember" in refusal


def test_forget_capped_at_two_per_tick():
    guard = _guard()
    assert guard("forget", {"name": "a"}) is None
    assert guard("forget", {"name": "b"}) is None
    refusal = guard("forget", {"name": "c"})
    assert refusal is not None and refusal.startswith("BLOCKED")


def test_caps_are_independent_and_per_instance():
    # remember's count must not consume forget's budget, and a fresh
    # tick (a new guard instance) starts the count over.
    guard = _guard()
    guard("remember", {"body": "x"})
    guard("remember", {"body": "y"})
    assert guard("forget", {"name": "a"}) is None  # untouched budget

    fresh = _guard()
    assert fresh("remember", {"body": "new tick, new budget"}) is None
