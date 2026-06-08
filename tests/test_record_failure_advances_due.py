"""Regression: a daily task that fails must advance its due_at to the
next interval; otherwise it re-fires on every heartbeat tick until
auto-cancel, spamming the user with failure notifications.

Bug was discovered live: the daily leetcode task failed once and the
user received fallback-notify pings every few minutes from each
subsequent heartbeat tick. The structural cause was that complete()
advanced due_at on recurring tasks but record_failure() didn't.
"""

from __future__ import annotations

import importlib
import sys
from datetime import datetime, timedelta
from pathlib import Path


def _store(tmp_path: Path):
    # Other tests in this suite stub `tasks` with a fake — force-reload
    # the real module so we exercise the actual TaskStore.
    sys.modules.pop("tasks", None)
    tasks = importlib.import_module("tasks")
    return tasks.TaskStore(tmp_path)


def test_recurring_daily_failure_advances_due_by_one_day(tmp_path):
    store = _store(tmp_path)
    yesterday = (datetime.now() - timedelta(hours=2)).isoformat(timespec="seconds")
    task = store.create(
        title="daily that fails",
        description="-",
        due_at=yesterday,
        recurrence="daily",
        notify=True,
    )
    original_due = datetime.fromisoformat(task["due_at"])

    updated = store.record_failure(task["id"], "something broke")
    new_due = datetime.fromisoformat(updated["due_at"])

    advanced = (new_due - original_due).total_seconds()
    # Anchor semantics (added 2026-06): _advance_due snaps to the anchor
    # on the NEXT calendar day after `now`, not exactly +24h from
    # original due_at. That's deliberate — prevents same-day double-
    # firing when a partial-recovery succeeds before today's anchor.
    # For this fixture (due_at ~2h ago, now in the same day past the
    # anchor), next fire is tomorrow-at-anchor → 24h gap.
    # If the test runs near midnight (due_at = yesterday-at-anchor,
    # now = today-just-past-midnight), next fire is still tomorrow-at-
    # anchor → 48h gap. Both are correct per the anchor contract.
    assert 23 * 3600 < advanced <= 49 * 3600, advanced
    assert updated["consecutive_failures"] == 1
    assert updated["status"] == "active"


def test_weekly_failure_advances_due_by_one_week(tmp_path):
    store = _store(tmp_path)
    due = (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds")
    task = store.create(
        title="weekly that fails",
        description="-",
        due_at=due,
        recurrence="weekly",
        notify=True,
    )
    original_due = datetime.fromisoformat(task["due_at"])

    updated = store.record_failure(task["id"], "broken")
    new_due = datetime.fromisoformat(updated["due_at"])
    advanced = (new_due - original_due).total_seconds()
    assert 6.5 * 24 * 3600 < advanced < 7.5 * 24 * 3600


def test_one_shot_failure_does_not_advance_due(tmp_path):
    store = _store(tmp_path)
    due = (datetime.now() - timedelta(minutes=5)).isoformat(timespec="seconds")
    task = store.create(
        title="one-shot",
        description="-",
        due_at=due,
        recurrence="none",
        notify=False,
    )
    original_due = task["due_at"]
    updated = store.record_failure(task["id"], "broken")
    assert updated["due_at"] == original_due


def test_transient_failure_does_not_advance_due(tmp_path):
    """increment_failures=False is the provider-exhaustion path — we
    want the task to retry on the next tick, not skip to tomorrow."""
    store = _store(tmp_path)
    due = (datetime.now() - timedelta(minutes=5)).isoformat(timespec="seconds")
    task = store.create(
        title="daily",
        description="-",
        due_at=due,
        recurrence="daily",
        notify=False,
    )
    original_due = task["due_at"]
    updated = store.record_failure(task["id"], "provider exhausted", increment_failures=False)
    assert updated["due_at"] == original_due
    assert updated.get("consecutive_failures", 0) == 0
