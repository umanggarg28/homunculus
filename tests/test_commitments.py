"""Phase 2 — commitments: inferred check-in tasks the agent records to follow
up proactively (OpenClaw pattern), stored as source='inferred' tasks."""

from __future__ import annotations

from homunculus.tasks import TaskStore
from tests.conftest import load_real_tool_submodule


def test_task_source_defaults_to_user(tmp_path):
    store = TaskStore(tmp_path)
    t = store.create("do x", due_at="2026-07-05T09:00:00")
    assert t["source"] == "user"


def test_task_source_can_be_inferred(tmp_path):
    store = TaskStore(tmp_path)
    t = store.create("check in", due_at="2026-07-05T09:00:00", source="inferred")
    assert t["source"] == "inferred"


def test_record_commitment_creates_inferred_notifying_task(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMUNCULUS_TASKS_DIR", str(tmp_path))
    sched = load_real_tool_submodule("scheduling")
    out = sched.record_commitment(
        "follow up on the report", "2026-07-04T18:00:00", "open_loop"
    )
    assert "Recorded commitment" in out
    tasks = sched._task_store().list("all")
    assert len(tasks) == 1
    assert tasks[0]["source"] == "inferred"
    assert tasks[0]["notify"] is True
    # open_loop has zero lead → fires at the given time.
    assert tasks[0]["due_at"].startswith("2026-07-04T18:00")


def test_event_check_in_fires_before_the_event(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMUNCULUS_TASKS_DIR", str(tmp_path))
    sched = load_real_tool_submodule("scheduling")
    # Interview at 3pm → event_check_in fires ~2h before (1pm), never AT 3pm.
    sched.record_commitment("wish Umang luck", "2026-07-04T15:00:00", "event_check_in")
    due = sched._task_store().list("all")[0]["due_at"]
    assert due.startswith("2026-07-04T13:00")


def test_deadline_check_fires_a_day_before(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMUNCULUS_TASKS_DIR", str(tmp_path))
    sched = load_real_tool_submodule("scheduling")
    sched.record_commitment("submit taxes", "2026-07-05T17:00:00", "deadline_check")
    due = sched._task_store().list("all")[0]["due_at"]
    assert due.startswith("2026-07-04T17:00")


def test_record_commitment_requires_event_at(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMUNCULUS_TASKS_DIR", str(tmp_path))
    sched = load_real_tool_submodule("scheduling")
    assert sched.record_commitment("something", "", "open_loop").startswith("ERROR")


def test_record_commitment_rejects_bad_kind(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMUNCULUS_TASKS_DIR", str(tmp_path))
    sched = load_real_tool_submodule("scheduling")
    assert sched.record_commitment("x", "2026-07-04T18:00:00", "bogus").startswith("ERROR")
