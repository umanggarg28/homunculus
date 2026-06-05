"""Resumable tasks — partial state + scratchpad lifecycle.

The architectural fix for "tasks fail when one tick isn't enough":
- Partial progress is a first-class state (not a failure).
- Scratchpads survive across attempts so the next run resumes.
- Complete_task clears the scratchpad (a delivered task is done).
- After MAX_CONSECUTIVE_PARTIALS, the task escalates to a real
  failure so the user finds out instead of looping silently.
"""

from __future__ import annotations

import importlib
import sys
from datetime import datetime, timedelta
from pathlib import Path


def _store(tmp_path: Path):
    sys.modules.pop("tasks", None)
    tasks = importlib.import_module("tasks")
    return tasks, tasks.TaskStore(tmp_path)


def _new_recurring(store, due_at: str):
    return store.create(
        title="test daily",
        description="-",
        due_at=due_at,
        recurrence="daily",
        notify=True,
    )


# ── mark_partial ────────────────────────────────────────────────────


def test_mark_partial_reschedules_in_ten_minutes(tmp_path):
    tasks, store = _store(tmp_path)
    due = (datetime.now() - timedelta(hours=2)).isoformat(timespec="seconds")
    task = _new_recurring(store, due)
    updated = store.mark_partial(task["id"], "provider 429")
    new_due = datetime.fromisoformat(updated["due_at"])
    delta_min = (new_due - datetime.now()).total_seconds() / 60
    # Default PARTIAL_RETRY_MINUTES is 10 — allow ±1 min for clock drift.
    assert 8 < delta_min < 12
    assert updated["status"] == "active"
    assert updated["consecutive_partials"] == 1
    assert updated["consecutive_failures"] == 0


def test_partial_clears_last_fired_for_immediate_retry(tmp_path):
    """If the dedupe stamp isn't cleared, RE_FIRE_SUPPRESSION_SECONDS
    keeps the task from firing on the next tick — we'd wait 30 min
    instead of the ~10 we just rescheduled for."""
    _, store = _store(tmp_path)
    due = (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds")
    task = _new_recurring(store, due)
    store.mark_fired(task["id"])
    updated = store.mark_partial(task["id"], "provider 429")
    assert updated.get("last_fired_at") is None


def test_partial_counter_resets_on_completion(tmp_path):
    _, store = _store(tmp_path)
    due = (datetime.now() - timedelta(hours=2)).isoformat(timespec="seconds")
    task = _new_recurring(store, due)
    store.mark_partial(task["id"], "blip")
    store.mark_partial(task["id"], "blip")
    completed = store.complete(task["id"], result="finally delivered")
    assert completed["consecutive_partials"] == 0


def test_three_consecutive_partials_escalate_to_failure(tmp_path):
    """MAX_CONSECUTIVE_PARTIALS=3 — after the third in a row the
    harness must escalate so the user gets a notification, not loop
    silently retrying forever."""
    tasks, store = _store(tmp_path)
    due = (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds")
    task = _new_recurring(store, due)
    store.mark_partial(task["id"], "first")
    store.mark_partial(task["id"], "second")
    escalated = store.mark_partial(task["id"], "third")
    # Escalation resets partials and bumps the failure counter so the
    # standard CONSECUTIVE_FAILURE_LIMIT auto-cancel path applies.
    assert escalated["consecutive_partials"] == 0
    assert escalated["consecutive_failures"] == 1
    # Status stays active until CONSECUTIVE_FAILURE_LIMIT — the user
    # gets a notification but the task isn't dead yet.
    assert escalated["status"] == "active"


# ── scratchpad lifecycle ──────────────────────────────────────────────


def test_scratchpad_persists_across_attempts(tmp_path):
    tasks, store = _store(tmp_path)
    due = (datetime.now() - timedelta(hours=2)).isoformat(timespec="seconds")
    task = _new_recurring(store, due)
    tasks.write_scratchpad(store.root, task["id"], "step 1 done\nstep 2 pending")
    store.mark_partial(task["id"], "ran out of iters")
    # Scratchpad must survive — that's the whole point.
    assert "step 1 done" in tasks.read_scratchpad(store.root, task["id"])


def test_scratchpad_is_clearable_after_completion(tmp_path):
    """The tools/scheduling.py complete_task wrapper calls
    clear_scratchpad after store.complete. We test the primitives
    here — the wiring is one-line and tested by the live flow.
    Other tests stub the `tools` package at sys.modules level, so
    importing it from this file is unreliable."""
    tasks, store = _store(tmp_path)
    due = (datetime.now() - timedelta(hours=2)).isoformat(timespec="seconds")
    task = _new_recurring(store, due)
    tasks.write_scratchpad(store.root, task["id"], "step 1 done")
    store.complete(task["id"], "delivered")
    tasks.clear_scratchpad(store.root, task["id"])
    assert tasks.read_scratchpad(store.root, task["id"]) == ""


def test_scratchpad_append_creates_then_appends(tmp_path):
    tasks, _ = _store(tmp_path)
    tasks.append_scratchpad(tmp_path, "tid", "first line")
    tasks.append_scratchpad(tmp_path, "tid", "second line")
    out = tasks.read_scratchpad(tmp_path, "tid")
    assert "first line" in out
    assert "second line" in out
    # No squashing
    assert out.count("\n") >= 1


def test_clear_scratchpad_is_idempotent(tmp_path):
    tasks, _ = _store(tmp_path)
    # No file yet — must not raise.
    tasks.clear_scratchpad(tmp_path, "ghost")
    tasks.write_scratchpad(tmp_path, "real", "x")
    tasks.clear_scratchpad(tmp_path, "real")
    tasks.clear_scratchpad(tmp_path, "real")  # second time is no-op
    assert tasks.read_scratchpad(tmp_path, "real") == ""


# ── continue_task tool ────────────────────────────────────────────────


def test_continue_task_pattern_via_primitives(tmp_path):
    """continue_task() = append_scratchpad + mark_partial. Test the
    composition without importing the full tools package (which other
    tests stub at sys.modules level)."""
    tasks, store = _store(tmp_path)
    due = (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds")
    task = _new_recurring(store, due)
    tasks.append_scratchpad(store.root, task["id"], "- [x] fetched problem statement")
    updated = store.mark_partial(task["id"], "fetched problem; solution next tick")
    assert updated["consecutive_partials"] == 1
    assert "fetched problem statement" in tasks.read_scratchpad(store.root, task["id"])
