"""Regression: tasks must fire on the user's wall clock, not container UTC.

The TaskStore used to call datetime.now() everywhere, which returns
container-local time. With a UTC container and an IST user, a task
scheduled for 9 AM IST stayed in the task list until 9 AM UTC — i.e.
2:30 PM IST, 5.5 hours late. due() now uses user-local naive time to
match what's written to the file.
"""

from __future__ import annotations

import importlib.util
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest


def _real_task_store():
    """Load the real TaskStore directly from tasks.py, bypassing any
    sys.modules stub a prior test may have installed."""
    spec = importlib.util.spec_from_file_location(
        "tasks_real_due_test", Path(__file__).parent.parent / "tasks.py"
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod.TaskStore


@pytest.fixture
def ist_user(tmp_path, monkeypatch):
    """Set the user's TZ to Asia/Kolkata, pointing at a tmp file."""
    tz_file = tmp_path / "user_tz.txt"
    tz_file.write_text("Asia/Kolkata", encoding="utf-8")
    monkeypatch.setenv("HOMUNCULUS_USER_TZ_FILE", str(tz_file))
    # Clear the in-module cache so the new env var takes effect.
    import user_tz
    user_tz._cached_name = None
    user_tz._cached_mtime = None
    yield


def test_task_due_at_user_local_fires_on_user_clock(tmp_path, ist_user):
    """A due_at written by the web UI is in user-local wall time; due()
    must consider it overdue once the user's clock is past it, regardless
    of the container's TZ."""
    TaskStore = _real_task_store()
    from user_tz import now_user_naive

    store = TaskStore(tmp_path)
    # Schedule for 1 minute ago in user-local time.
    past = (now_user_naive() - timedelta(minutes=1)).isoformat(timespec="seconds")
    store.create(
        title="due now",
        description="should fire",
        due_at=past,
        recurrence="none",
    )

    due_now = store.due()
    assert len(due_now) == 1
    assert due_now[0]["title"] == "due now"


def test_future_user_local_task_is_not_due(tmp_path, ist_user):
    """A due_at 1 hour in the future (user-local) must not be flagged
    as due."""
    TaskStore = _real_task_store()
    from user_tz import now_user_naive

    store = TaskStore(tmp_path)
    future = (now_user_naive() + timedelta(hours=1)).isoformat(timespec="seconds")
    store.create(title="later", description="", due_at=future, recurrence="none")
    assert store.due() == []


def test_due_does_not_depend_on_container_tz(tmp_path, ist_user, monkeypatch):
    """Even if the process inherits a wildly different TZ env, due()
    must keep using user wall time."""
    TaskStore = _real_task_store()
    from user_tz import now_user_naive

    # Simulate the container running in UTC: set TZ env to UTC.
    monkeypatch.setenv("TZ", "UTC")
    store = TaskStore(tmp_path)
    past = (now_user_naive() - timedelta(seconds=30)).isoformat(timespec="seconds")
    store.create(title="should fire", description="", due_at=past, recurrence="none")
    assert len(store.due()) == 1
