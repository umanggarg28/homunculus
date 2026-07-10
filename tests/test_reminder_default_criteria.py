"""Notifying tasks get harness-owned delivery criteria.

Live failure 2026-07-09 ("Respond to Memorang"): the model invented a
success_criteria argument to create_task (which has no such parameter,
on purpose), the argument was silently dropped, and the task stored
with NO criteria. At fire time the agent sent a 34-char nonsense
notify ("Memorang response: task completed.") and complete_task passed
with the false claim "Responded to Memorang as required."

The contract for "a reminder fired" belongs to the harness: any
notify=True task now stores notify_called + notify_contains(title)
defaults, so the TaskGuard pre-send check rejects a notify that never
mentions the reminder and complete_task stays blocked until one does.
"""

from __future__ import annotations

import pytest

from tests.conftest import load_real_tool_submodule

load_real_tool_submodule("_state")
load_real_tool_submodule("_intake")
_sched = load_real_tool_submodule("scheduling")
from homunculus.tasks import TaskStore  # noqa: E402


@pytest.fixture()
def tasks_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMUNCULUS_TASKS_DIR", str(tmp_path))
    monkeypatch.setenv("HOMUNCULUS_USER_TZ_FILE", str(tmp_path / "tz.txt"))
    return tmp_path


def test_notifying_reminder_gets_delivery_criteria(tasks_dir):
    out = _sched.create_task(
        "Respond to Memorang",
        due_at="2027-07-09T12:00:00+05:30",
        notify=True,
    )
    assert out.startswith("Created task")
    store = TaskStore(tasks_dir)
    task = next(t for t in store.list("all") if t["title"] == "Respond to Memorang")
    assert {"type": "notify_called"} in task["success_criteria"]
    assert {"type": "notify_contains", "text": "Respond to Memorang"} in task["success_criteria"]


def test_silent_task_gets_no_criteria(tasks_dir):
    _sched.create_task(
        "Track something quietly",
        description="internal bookkeeping",
        due_at="2027-07-09T12:00:00+05:30",
        notify=False,
    )
    store = TaskStore(tasks_dir)
    task = next(t for t in store.list("all") if t["title"] == "Track something quietly")
    assert task["success_criteria"] == []


def test_commitment_check_in_gets_delivery_criteria(tasks_dir):
    out = _sched.record_commitment(
        "wish Umang luck before the interview",
        "2027-07-09T15:00:00+05:30",
        kind="event_check_in",
    )
    assert out.startswith("Recorded commitment")
    store = TaskStore(tasks_dir)
    task = next(
        t for t in store.list("all")
        if t["title"] == "wish Umang luck before the interview"
    )
    assert {"type": "notify_called"} in task["success_criteria"]
    assert {
        "type": "notify_contains",
        "text": "wish Umang luck before the interview",
    } in task["success_criteria"]
