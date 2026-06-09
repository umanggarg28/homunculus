"""Recurring tasks anchor to their original time-of-day across cycles.

Regression: morning-brief was created for 7 AM IST. Each partial-recovery
shift advanced due_at by +10 min, and `_advance_due` did `due_at + 1 day`
without snapping back. Over ~15 cycles the task drifted from 7 AM → 1:30 AM
and the user stopped getting their morning brief during waking hours.

With `recur_anchor` set, `_advance_due` snaps to the anchored HH:MM:SS
on the next valid calendar day no matter how badly `due_at` drifted.
"""

from __future__ import annotations

import importlib.util
from datetime import datetime
from pathlib import Path


def _real_task_store():
    """Load TaskStore directly so cross-test sys.modules stubs (set up by
    test_schema_validation / test_output_guard) don't replace it with a
    _FakeStore."""
    spec = importlib.util.spec_from_file_location(
        "tasks_real_anchor_test", Path(__file__).parent.parent / "tasks.py"
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod.TaskStore


TaskStore = _real_task_store()


def test_advance_due_snaps_to_anchor_after_drift():
    """A `daily` task whose due_at drifted into the night should snap
    back to its 7 AM anchor on the next valid day.

    Updated 2026-06-09: the prior assertion was `parsed.day == 8`
    (tomorrow). That implemented cycle semantics (next = +1 day from
    now), which had the perverse side effect of silently skipping the
    user's wake-time delivery whenever the task fired early. Now we
    implement anchor semantics (next = next calendar anchor strictly
    after now). After a drifted 1:40 AM fire, today's 7 AM is still
    in the future and gets restored — one extra fire that day, then
    the schedule snaps clean. Better than skipping today entirely."""
    drifted_due = "2026-06-07T01:40:00"
    now = datetime.fromisoformat("2026-06-07T01:45:00")
    next_due = TaskStore._advance_due(drifted_due, "daily", now, recur_anchor="07:00:00")
    parsed = datetime.fromisoformat(next_due)
    assert parsed.hour == 7 and parsed.minute == 0 and parsed.second == 0
    assert parsed.day == 7  # today, restoring the anchor — was 8 under cycle semantics


def test_advance_due_anchor_skips_already_past_anchor_today():
    """If today's anchor time is already past, advance to tomorrow."""
    now = datetime.fromisoformat("2026-06-07T10:00:00")
    next_due = TaskStore._advance_due("2026-06-07T09:00:00", "daily", now, recur_anchor="09:00:00")
    parsed = datetime.fromisoformat(next_due)
    assert parsed.day == 8
    assert parsed.hour == 9


def test_advance_due_anchor_preserves_today_if_not_yet_reached():
    """REGRESSION: an early fire (before today's anchor) must NOT skip
    today's slot. Bug live in prod 2026-06-09: a manual test fire at
    02:12 IST of a 9 AM daily task scheduled the next run for tomorrow
    9 AM instead of today 9 AM, silently missing the user-facing
    delivery for that day. The fix: start `base` at today's anchor,
    only advance one step if today's anchor is already in the past."""
    # Fire happened at 02:12 IST on June 9 (before today's 9 AM anchor).
    now = datetime.fromisoformat("2026-06-09T02:12:06")
    next_due = TaskStore._advance_due(
        "2026-06-09T09:00:00",  # was today's anchor
        "daily",
        now,
        recur_anchor="09:00:00",
    )
    parsed = datetime.fromisoformat(next_due)
    assert parsed.day == 9, (
        f"early fire must preserve today's anchor, got: {parsed.isoformat()}"
    )
    assert parsed.hour == 9 and parsed.minute == 0


def test_advance_due_anchor_returns_today_when_now_is_right_at_midnight():
    """Edge: fire at 00:00:01 sharp — today's anchor (e.g., 9 AM) is
    still ~9 hours in the future, must return today's anchor."""
    now = datetime.fromisoformat("2026-06-09T00:00:01")
    next_due = TaskStore._advance_due(
        "2026-06-08T09:00:00", "daily", now, recur_anchor="09:00:00",
    )
    parsed = datetime.fromisoformat(next_due)
    assert parsed.day == 9 and parsed.hour == 9


def test_advance_due_anchor_exact_match_advances_to_next():
    """Edge: now == today's anchor (down to the second). The contract
    is `strictly after now`, so we must advance to tomorrow's anchor.
    This is the normal post-success path — after a 9 AM fire completes
    at ~09:00:05, we want the next fire tomorrow at 9 AM, not today."""
    now = datetime.fromisoformat("2026-06-09T09:00:00")
    next_due = TaskStore._advance_due(
        "2026-06-09T09:00:00", "daily", now, recur_anchor="09:00:00",
    )
    parsed = datetime.fromisoformat(next_due)
    assert parsed.day == 10 and parsed.hour == 9


def test_advance_due_weekly_anchor_steps_by_seven_days():
    now = datetime.fromisoformat("2026-06-07T08:00:00")
    next_due = TaskStore._advance_due("2026-06-07T07:00:00", "weekly", now, recur_anchor="07:00:00")
    parsed = datetime.fromisoformat(next_due)
    assert parsed.day == 14  # +7 days
    assert parsed.hour == 7


def test_advance_due_without_anchor_uses_legacy_behavior():
    """Tasks created before this fix have no anchor — must still work."""
    now = datetime.fromisoformat("2026-06-07T08:00:00")
    next_due = TaskStore._advance_due("2026-06-06T07:00:00", "daily", now, recur_anchor=None)
    parsed = datetime.fromisoformat(next_due)
    # Legacy: due_at + 1 day past now → 06-08T07:00 (passes now)
    assert parsed > now


def test_advance_due_malformed_anchor_falls_back():
    """A garbage anchor string must not crash — fall back to legacy."""
    now = datetime.fromisoformat("2026-06-07T08:00:00")
    next_due = TaskStore._advance_due(
        "2026-06-06T07:00:00", "daily", now, recur_anchor="not-a-time",
    )
    parsed = datetime.fromisoformat(next_due)
    assert parsed > now  # didn't crash, advanced normally


def test_create_recurring_task_records_anchor(tmp_path):
    """create() must persist recur_anchor from the due_at time-of-day."""
    store = TaskStore(tmp_path)
    task = store.create(
        title="Morning brief",
        description="...",
        due_at="2026-06-08T07:00:00",
        recurrence="daily",
        notify=True,
    )
    assert task["recur_anchor"] == "07:00:00"


def test_create_one_shot_task_no_anchor(tmp_path):
    """Non-recurring tasks must NOT get an anchor (would be misleading)."""
    store = TaskStore(tmp_path)
    task = store.create(
        title="One-off ping",
        description="...",
        due_at="2026-06-08T07:00:00",
        recurrence="none",
        notify=True,
    )
    assert task.get("recur_anchor") is None
