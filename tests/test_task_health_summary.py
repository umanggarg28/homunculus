"""task_health_summary must classify deterministically.

The bug the brief had: LLM reading raw last_runs and pattern-matching
on "I see failures" → alerts for tasks that already recovered. Moving
the judgment into Python so the LLM only formats.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types
from datetime import timedelta
from pathlib import Path

import pytest

# tools.notify stub so the import chain works (mirrors test_pre_turn_hook).
if "homunculus.tools.notify" not in sys.modules:
    _stub = types.ModuleType("homunculus.tools.notify")
    _stub._send_to_telegram = lambda *_a, **_kw: None
    sys.modules["homunculus.tools.notify"] = _stub


def _real_task_store():
    spec = importlib.util.spec_from_file_location(
        "tasks_real_for_health", Path(__file__).parent.parent / "homunculus" / "tasks.py"
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod.TaskStore


@pytest.fixture
def store_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMUNCULUS_TASKS_DIR", str(tmp_path / "tasks"))
    TaskStore = _real_task_store()
    return TaskStore(tmp_path / "tasks")


def _summary() -> dict:
    # Import the pure function directly — bypasses the conftest tools
    # stub and exercises the same code path used by tools/scheduling.py.
    spec = importlib.util.spec_from_file_location(
        "tasks_for_health", Path(__file__).parent.parent / "homunculus" / "tasks.py"
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    store = mod.TaskStore(Path(os.environ["HOMUNCULUS_TASKS_DIR"]))
    return mod.task_health_summary(store)


def test_recovered_task_is_not_in_alerts(store_in_tmp, monkeypatch):
    """The headline regression: a task with many old failures followed
    by a recent success must NOT show up as an alert."""
    from homunculus.user_tz import now_user_naive
    now = now_user_naive()
    runs = []
    # 5 failures, all within failure window
    for h in range(5):
        runs.append({
            "ts": (now - timedelta(hours=10 - h)).isoformat(timespec="seconds"),
            "status": "failure",
            "result": "transient",
        })
    # latest run is a success
    runs.append({
        "ts": (now - timedelta(hours=1)).isoformat(timespec="seconds"),
        "status": "success",
        "result": "Delivered.",
    })

    store_in_tmp.create(
        title="daily leetcode",
        description="x",
        due_at=(now + timedelta(hours=10)).isoformat(timespec="seconds"),
        recurrence="daily",
    )
    tasks = store_in_tmp.all()
    tasks[0]["last_runs"] = runs
    store_in_tmp._write(tasks)

    summary = _summary()
    assert summary["alerts"] == []
    # And it should show up as recovered (5 prior failures, latest success).
    assert any(
        r["title"] == "daily leetcode"
        and r["latest_status"] == "success"
        and r["prior_failure_count"] == 5
        for r in summary["recently_recovered"]
    )


def test_task_with_latest_failure_appears_in_alerts(store_in_tmp):
    """The reverse: most recent run IS a failure, within 36h → alert."""
    from homunculus.user_tz import now_user_naive
    now = now_user_naive()
    store_in_tmp.create(title="brittle", description="", due_at=None, recurrence="none")
    tasks = store_in_tmp.all()
    tasks[0]["last_runs"] = [
        {
            "ts": (now - timedelta(hours=2)).isoformat(timespec="seconds"),
            "status": "failure",
            "result": "provider down",
        },
    ]
    store_in_tmp._write(tasks)

    summary = _summary()
    assert len(summary["alerts"]) == 1
    assert summary["alerts"][0]["title"] == "brittle"
    assert summary["recently_recovered"] == []


def test_old_failure_outside_window_is_ignored(store_in_tmp):
    """A failure from a week ago is not actionable."""
    from homunculus.user_tz import now_user_naive
    now = now_user_naive()
    store_in_tmp.create(title="old issue", description="", due_at=None, recurrence="none")
    tasks = store_in_tmp.all()
    tasks[0]["last_runs"] = [
        {
            "ts": (now - timedelta(days=7)).isoformat(timespec="seconds"),
            "status": "failure",
            "result": "ancient",
        },
    ]
    store_in_tmp._write(tasks)

    summary = _summary()
    assert summary["alerts"] == []


def test_today_commitments_includes_next_24h_only(store_in_tmp):
    from homunculus.user_tz import now_user_naive
    now = now_user_naive()
    # Three tasks: due in 5h, due in 30h (skip), due in 2 days (skip)
    for hours, title in [(5, "today"), (30, "tomorrow"), (48, "later")]:
        store_in_tmp.create(
            title=title,
            description="",
            due_at=(now + timedelta(hours=hours)).isoformat(timespec="seconds"),
            recurrence="none",
        )
    summary = _summary()
    titles = [c["title"] for c in summary["today_commitments"]]
    assert "today" in titles
    assert "tomorrow" not in titles
    assert "later" not in titles
