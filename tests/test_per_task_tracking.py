"""Per-task token + cost tracking.

attribute_usage_to_last_run retrofits LLM usage metrics onto the most
recent run entry. _append_run accepts an optional usage dict directly.
Together they let heartbeat and run-now attribute LLM spend to the
task that drove it, without plumbing context through the agent.
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


def _new_task(store):
    return store.create(
        title="test",
        description="-",
        due_at=(datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds"),
        recurrence="daily",
        notify=True,
    )


def test_append_run_records_usage(tmp_path):
    _, store = _store(tmp_path)
    task = _new_task(store)
    usage = {
        "input_tokens": 1234,
        "output_tokens": 56,
        "cached_tokens": 7,
        "cost_cents": 0.0123,
        "calls": 3,
    }
    updated = store.mark_partial(task["id"], "still working", usage=usage)
    last = updated["last_runs"][-1]
    assert last["input_tokens"] == 1234
    assert last["output_tokens"] == 56
    assert last["cached_tokens"] == 7
    assert last["cost_cents"] == 0.0123
    assert last["calls"] == 3


def test_attribute_usage_to_last_run_retrofits(tmp_path):
    """The success path appends a run via the tool layer (no usage
    visible there). The orchestrator then calls
    attribute_usage_to_last_run to add the metrics post-hoc."""
    _, store = _store(tmp_path)
    task = _new_task(store)
    # Simulate the tool layer appending a success run with no metrics.
    store.complete(task["id"], result="delivered")
    refreshed = store.get(task["id"])
    assert "input_tokens" not in refreshed["last_runs"][-1]

    store.attribute_usage_to_last_run(
        task["id"],
        {"input_tokens": 5000, "output_tokens": 100, "cost_cents": 0.05, "calls": 4},
    )
    final = store.get(task["id"])
    last = final["last_runs"][-1]
    assert last["input_tokens"] == 5000
    assert last["output_tokens"] == 100
    assert last["cost_cents"] == 0.05
    assert last["calls"] == 4


def test_attribute_usage_idempotent(tmp_path):
    """Re-attribution overwrites, doesn't accumulate. Important if
    the orchestrator's post-tick scan runs twice (e.g. on retry)."""
    _, store = _store(tmp_path)
    task = _new_task(store)
    store.complete(task["id"], result="done")
    store.attribute_usage_to_last_run(task["id"], {"input_tokens": 100})
    store.attribute_usage_to_last_run(task["id"], {"input_tokens": 100})
    last = store.get(task["id"])["last_runs"][-1]
    assert last["input_tokens"] == 100


def test_attribute_usage_no_op_on_empty_runs(tmp_path):
    _, store = _store(tmp_path)
    task = _new_task(store)
    # Task exists but has no runs yet.
    store.attribute_usage_to_last_run(task["id"], {"input_tokens": 999})
    refreshed = store.get(task["id"])
    assert refreshed["last_runs"] == []


def test_attribute_usage_no_op_on_missing_task(tmp_path):
    """Must not raise — heartbeat may call this for a task that was
    cancelled mid-tick."""
    _, store = _store(tmp_path)
    store.attribute_usage_to_last_run("ghost-task", {"input_tokens": 1})


def test_empty_usage_dict_does_not_pollute_run(tmp_path):
    """Passing an empty usage dict (e.g. no LLM activity for a task)
    must leave the run record clean — no zero-valued tracking fields."""
    _, store = _store(tmp_path)
    task = _new_task(store)
    updated = store.mark_partial(task["id"], "x", usage={})
    last = updated["last_runs"][-1]
    assert "input_tokens" not in last
    assert "cost_cents" not in last
