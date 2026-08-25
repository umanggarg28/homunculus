"""No task may carry a success criterion the delivery gate makes impossible.

Two gates, each correct alone. `notify_contains` demands a substring in the
delivered text; the notify gate refuses any delivered text carrying a failure
sentinel. A criterion whose substring holds a sentinel contradicts the gate —
include it and the send is blocked, omit it and the criterion fails — so the
task is impossible before it runs and burns its retries proving so.

Observed live: `record_commitment` derives the substring from the task title,
and the thing worth following up on was an outage, giving

    notify_contains: "check whether email-event-watch's gmail source is
                      working (persistent GMAIL_UNAVAILABLE)"

Same shape as the complete_task/record_failure deadlock, in the delivery path.

The check lives with the EVALUATOR, not with any producer. Criteria arrive
from `_default_reminder_criteria`, from a skill's `success_criteria`
frontmatter, and from the `_plan_tick` fold; fixing whichever one happened to
fail leaves the trap in the others and in every task already on disk.
"""

from __future__ import annotations

import sys
import types

import pytest

if "homunculus.tools.notify" not in sys.modules:
    _stub = types.ModuleType("homunculus.tools.notify")
    _stub._send_to_telegram = lambda *_a, **_kw: None
    _stub.deliver = lambda *_a, **_kw: None
    sys.modules["homunculus.tools.notify"] = _stub

from homunculus.sentinels import SENTINELS, find_sentinel  # noqa: E402
from homunculus.task_guard import TaskGuard, unsatisfiable_criteria  # noqa: E402

BAD = "check whether gmail is working (persistent GMAIL_UNAVAILABLE)"


def test_an_ordinary_criterion_is_satisfiable():
    assert unsatisfiable_criteria([{"type": "notify_contains", "text": "Renew passport"}]) == []


@pytest.mark.parametrize("sentinel", SENTINELS)
def test_every_sentinel_shape_is_detected(sentinel):
    """Both spellings — three of the seven are space-separated."""
    c = [{"type": "notify_contains", "text": f"follow up on {sentinel} today"}]
    assert unsatisfiable_criteria(c) == c


def test_other_criterion_types_are_untouched():
    c = [{"type": "notify_called"}, {"type": "notify_min_chars", "n": 40}]
    assert unsatisfiable_criteria(c) == []


# ------------------------------------------------------- the property itself

def test_a_deliverable_message_exists_for_every_accepted_criterion():
    """The invariant, stated directly: whatever the guard still enforces must
    be satisfiable by a message the notify gate would let through."""
    task = "t"
    guard = TaskGuard({task: [
        {"type": "notify_called"},
        {"type": "notify_contains", "text": BAD},
    ]})
    # A delivery that quotes the title is refused by the sentinel gate...
    assert guard.on_tool_call("notify", {"text": f"Update: {BAD}"}) is not None
    # ...so a clean delivery must be able to satisfy what remains.
    assert guard.on_tool_call("notify", {"text": "Gmail is still not connected."}) is None
    assert guard.criteria_failures(task) == []


def test_the_task_is_no_longer_deadlocked_end_to_end():
    """Reproduction of the live failure: it must now be closable."""
    task = "check-gmail"
    guard = TaskGuard({task: [
        {"type": "notify_called"},
        {"type": "notify_contains", "text": BAD},
    ]})
    assert guard.on_tool_call("notify", {"text": "Checked: Gmail still not connected."}) is None
    assert guard.on_tool_call("complete_task", {"task_id": task}) is None


def test_a_real_contains_requirement_is_still_enforced():
    """The drop must be narrow — a satisfiable substring rule keeps its teeth."""
    task = "t"
    guard = TaskGuard({task: [
        {"type": "notify_called"},
        {"type": "notify_contains", "text": "dentist"},
    ]})
    assert guard.on_tool_call("notify", {"text": "Unrelated message."}) is not None
    assert guard.on_tool_call("notify", {"text": "Reminder: dentist at 4pm."}) is None


def test_doctor_reports_rather_than_silently_dropping():
    """Ignoring it at run time is not the same as nobody knowing."""
    from homunculus import doctor

    findings = doctor.audit_unsatisfiable_criteria([
        {"id": "check-gmail", "success_criteria": [{"type": "notify_contains", "text": BAD}]},
        {"id": "fine", "success_criteria": [{"type": "notify_contains", "text": "dentist"}]},
    ])
    assert len(findings) == 1
    assert findings[0].subject == "check-gmail"
    assert find_sentinel(BAD) in BAD
