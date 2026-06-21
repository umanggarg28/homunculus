"""run_now() on a cancelled task starts a fresh failure budget.

A recurring task auto-cancels after consecutive_failure_limit failures. The
counter survives the cancel, so reactivating the task without clearing it would
re-cancel on the very next failure — making "run now" a one-shot that silently
dies again. run_now() clears the counter when (and only when) it reactivates a
cancelled task, so a manual restart is a genuine reset.

The live case: quiz-coach auto-cancelled after Telegram-timeout failures that a
later fix made impossible. The cancellation was stale, but reactivating it would
have re-cancelled on the first hiccup without this reset.
"""

from __future__ import annotations

import importlib
import sys
from datetime import datetime, timedelta
from pathlib import Path


def _store(tmp_path: Path):
    sys.modules.pop("homunculus.tasks", None)
    tasks = importlib.import_module("homunculus.tasks")
    return tasks, tasks.TaskStore(tmp_path)


def _new_task(store):
    return store.create(
        title="test",
        description="-",
        due_at=(datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds"),
        recurrence="daily",
        notify=True,
    )


def _cancel_via_failures(tasks_mod, store, task_id):
    """Drive the task to auto-cancel by exhausting the failure budget."""
    limit = tasks_mod.get_config().task.consecutive_failure_limit
    for _ in range(limit):
        store.record_failure(task_id, "notify timed out")
    return store.get(task_id)


def test_run_now_reactivates_cancelled_task_and_resets_counter(tmp_path):
    tasks_mod, store = _store(tmp_path)
    task = _new_task(store)

    cancelled = _cancel_via_failures(tasks_mod, store, task["id"])
    assert cancelled["status"] == "cancelled"
    assert cancelled["consecutive_failures"] >= (
        tasks_mod.get_config().task.consecutive_failure_limit
    )

    reactivated = store.run_now(task["id"])
    assert reactivated["status"] == "active"
    assert reactivated["consecutive_failures"] == 0

    # One subsequent failure must NOT immediately re-cancel — the budget is fresh.
    after_one = store.record_failure(task["id"], "transient blip")
    assert after_one["status"] == "active"
    assert after_one["consecutive_failures"] == 1


def test_run_now_on_active_task_leaves_counter_untouched(tmp_path):
    """run_now is also used to trigger an already-active task immediately;
    that path must not silently forgive accumulated failures."""
    tasks_mod, store = _store(tmp_path)
    task = _new_task(store)
    store.record_failure(task["id"], "one failure")  # counter -> 1, still active

    reactivated = store.run_now(task["id"])
    assert reactivated["status"] == "active"
    assert reactivated["consecutive_failures"] == 1
