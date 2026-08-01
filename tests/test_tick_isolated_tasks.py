"""Each due task runs in its OWN isolated agent loop.

Multiplexing several due tasks into one shared agent loop let the weak model
cross-contaminate them — observed live 2026-06-23: the morning brief was
delivered, then record_failure'd while the same loop juggled the leetcode
task. The fix runs one isolated loop per due task (the OpenClaw
`src/cron/isolated-agent` / Letta EphemeralAgent pattern). These tests pin the
control flow: tick() calls the isolated runner once per due task, never once
for the batch.
"""

from __future__ import annotations

import importlib
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path

if "homunculus.tools.notify" not in sys.modules:
    _stub = types.ModuleType("homunculus.tools.notify")
    _stub._send_to_telegram = lambda *_a, **_kw: None
    sys.modules["homunculus.tools.notify"] = _stub


def _real_task_store(tmp_path: Path):
    # Other suites stub `tasks` with a fake — force the real module so we
    # exercise the actual TaskStore + due() logic.
    sys.modules.pop("homunculus.tasks", None)
    return importlib.import_module("homunculus.tasks").TaskStore(tmp_path)


def _setup(tmp_path, monkeypatch, n_due: int):
    monkeypatch.setenv("HOMUNCULUS_TASKS_DIR", str(tmp_path))
    monkeypatch.setenv("HOMUNCULUS_MEMORY_DIR", str(tmp_path / "memory"))
    store = _real_task_store(tmp_path)
    overdue = (datetime.now() - timedelta(minutes=5)).isoformat(timespec="seconds")
    for i in range(n_due):
        store.create(
            title=f"due task {i}",
            description="-",
            due_at=overdue,
            recurrence="daily",
            notify=False,
        )
    return store


def _patch_heartbeat(monkeypatch):
    """Stub the parts of tick() we don't exercise and capture isolated runs."""
    from homunculus import heartbeat
    import homunculus.tools as tools_module

    monkeypatch.setattr(
        heartbeat.agent_controls, "load_controls",
        lambda: types.SimpleNamespace(paused=False),
    )
    monkeypatch.setattr(heartbeat.events, "emit", lambda *a, **k: None)
    # A healthy (non-empty) tool registry — the outage guard skips task
    # runs entirely when SCHEMAS is empty (see test_tool_registry_outage).
    monkeypatch.setattr(
        tools_module, "SCHEMAS",
        [{"function": {"name": "notify"}}], raising=False,
    )

    calls: list[tuple[str, int, int]] = []

    def _fake_isolated(memory, model, tasks, task, memory_root, now_iso, idx, total):
        calls.append((task["id"], idx, total))

    monkeypatch.setattr(heartbeat, "_run_task_isolated", _fake_isolated)
    return heartbeat, calls


def test_two_due_tasks_run_in_separate_isolated_loops(tmp_path, monkeypatch):
    store = _setup(tmp_path, monkeypatch, n_due=2)
    heartbeat, calls = _patch_heartbeat(monkeypatch)
    due_ids = {t["id"] for t in store.due()}

    heartbeat.tick(memory=types.SimpleNamespace(), model=None)

    # One isolated run per due task — never a single batched call.
    assert len(calls) == 2
    assert {c[0] for c in calls} == due_ids
    # Each call sees the same total and a distinct 1-based index.
    assert sorted(c[1] for c in calls) == [1, 2]
    assert all(c[2] == 2 for c in calls)


def test_one_task_failing_does_not_abort_the_others(tmp_path, monkeypatch):
    """A real (non-network) error in one task's loop must not stop the
    sibling tasks from running."""
    store = _setup(tmp_path, monkeypatch, n_due=3)
    heartbeat, calls = _patch_heartbeat(monkeypatch)
    due_ids = {t["id"] for t in store.due()}

    # Make the first-invoked task raise a non-transient error.
    first_raised = {}

    def _boom(memory, model, tasks, task, memory_root, now_iso, idx, total):
        calls.append((task["id"], idx, total))
        if len(calls) == 1:
            first_raised["id"] = task["id"]
            raise ValueError("task-specific bug")

    monkeypatch.setattr(heartbeat, "_run_task_isolated", _boom)
    heartbeat.tick(memory=types.SimpleNamespace(), model=None)

    # All three still attempted despite the first raising.
    assert len(calls) == 3
    assert {c[0] for c in calls} == due_ids


def test_transient_network_error_propagates_for_backoff(tmp_path, monkeypatch):
    """A network-class error re-raises out of tick() so main() applies its
    short retry — completed tasks already advanced their due_at."""
    _setup(tmp_path, monkeypatch, n_due=2)
    heartbeat, calls = _patch_heartbeat(monkeypatch)

    def _net_fail(memory, model, tasks, task, memory_root, now_iso, idx, total):
        calls.append((task["id"], idx, total))
        raise ConnectionError("[Errno 101] Network is unreachable")

    monkeypatch.setattr(heartbeat, "_run_task_isolated", _net_fail)

    import pytest
    with pytest.raises(ConnectionError):
        heartbeat.tick(memory=types.SimpleNamespace(), model=None)
    # Propagated on the first task — the loop did not swallow it.
    assert len(calls) == 1
