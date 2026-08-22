"""Every run must always have one honest exit.

TaskGuard gates both close-out verbs: `complete_task` is refused when the
harness can prove the run did not succeed, `record_failure` when it can prove
it did. The design constraint binding them is that the two can never refuse
the same run — a weak model boxed in on all exits ends the loop in prose, and
prose settles as a silent drop.

That invariant was asserted in a comment and violated in production. On
2026-08-22 the email-event-watch task ran three times: gmail_search returned
GMAIL_UNAVAILABLE, the model delivered an honest "email isn't connected"
notice (satisfying every criterion), and then complete_task refused ("every
data source failed, call record_failure") while record_failure refused ("the
delivery went out, call complete_task"). Each refusal named the other. The
task burned three attempts and escalated to a real failure.

These tests pin the invariant itself rather than that one scenario, so a
future gate added to one verb cannot silently close the last exit.
"""

from __future__ import annotations

import itertools
import sys
import types

import pytest

if "homunculus.tools.notify" not in sys.modules:
    _stub = types.ModuleType("homunculus.tools.notify")
    _stub._send_to_telegram = lambda *_a, **_kw: None
    sys.modules["homunculus.tools.notify"] = _stub

from homunculus.task_guard import TaskGuard  # noqa: E402

TASK = "watch"


def _guard(criteria, required):
    return TaskGuard({TASK: criteria}, required_calls_by_task={TASK: required})


def _exits_open(guard) -> list[str]:
    """Which close-out verbs the guard would currently allow."""
    open_exits = []
    for verb in ("complete_task", "record_failure"):
        probe = _clone(guard)
        if probe.on_tool_call(verb, {"task_id": TASK}) is None:
            open_exits.append(verb)
    return open_exits


def _clone(guard):
    """A shallow copy so probing one verb doesn't mark the task closed."""
    import copy

    return copy.deepcopy(guard)


def test_reproduces_the_2026_08_22_deadlock():
    """The exact production scenario: sole source down, honest notice sent."""
    guard = _guard([{"type": "notify_called"}], ["gmail_search"])
    guard.on_tool_call("gmail_search", {})
    guard.observe_tool_result(
        "gmail_search",
        "GMAIL_UNAVAILABLE: Google account not connected (or the request failed).",
    )
    guard.on_tool_call("notify", {"text": "Event watch — email isn't connected right now."})

    assert guard.every_required_source_failed(TASK) == ["gmail_search"]
    assert guard.criteria_failures(TASK) == []

    assert _exits_open(guard), (
        "both close-out verbs refused: the run has no honest exit and will "
        "end in prose, which settles as a silent drop"
    )
    # record_failure is the correct exit here — the source really was down.
    assert "record_failure" in _exits_open(guard)


@pytest.mark.parametrize(
    "source_ok,called_source,delivered",
    list(itertools.product([True, False], repeat=3)),
)
def test_some_exit_is_always_open(source_ok, called_source, delivered):
    """Across every combination of run state, at least one verb is allowed."""
    guard = _guard([{"type": "notify_called"}], ["gmail_search"])
    if called_source:
        guard.on_tool_call("gmail_search", {})
        guard.observe_tool_result(
            "gmail_search",
            "- a real result" if source_ok else "GMAIL_UNAVAILABLE: not connected.",
        )
    if delivered:
        guard.on_tool_call("notify", {"text": "Here is the update for today."})

    assert _exits_open(guard), (
        f"deadlock: source_ok={source_ok} called={called_source} "
        f"delivered={delivered} — neither complete_task nor record_failure "
        f"is allowed"
    )


def test_delivered_success_still_cannot_be_stamped_a_failure():
    """The mirror gate keeps its teeth: a genuinely good run can't self-fail."""
    guard = _guard([{"type": "notify_called"}], ["gmail_search"])
    guard.on_tool_call("gmail_search", {})
    guard.observe_tool_result("gmail_search", "- Interview Thursday 3pm")
    guard.on_tool_call("notify", {"text": "You have an interview Thursday at 3pm."})

    assert _exits_open(guard) == ["complete_task"]


def test_undelivered_run_still_cannot_be_completed():
    """And the complete gate keeps its teeth: nothing delivered, no success."""
    guard = _guard([{"type": "notify_called"}], ["gmail_search"])
    guard.on_tool_call("gmail_search", {})
    guard.observe_tool_result("gmail_search", "- Interview Thursday 3pm")

    assert _exits_open(guard) == ["record_failure"]
