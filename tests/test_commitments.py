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
        "check in before Umang's interview", "2026-07-04T18:00:00", "deadline_check"
    )
    assert "Recorded commitment" in out
    tasks = sched._task_store().list("all")
    assert len(tasks) == 1
    assert tasks[0]["source"] == "inferred"
    assert tasks[0]["notify"] is True
    assert tasks[0]["due_at"].startswith("2026-07-04T18:00")


def test_record_commitment_requires_check_at(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMUNCULUS_TASKS_DIR", str(tmp_path))
    sched = load_real_tool_submodule("scheduling")
    assert sched.record_commitment("something", "", "open_loop").startswith("ERROR")


def test_record_commitment_rejects_bad_kind(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMUNCULUS_TASKS_DIR", str(tmp_path))
    sched = load_real_tool_submodule("scheduling")
    assert sched.record_commitment("x", "2026-07-04T18:00:00", "bogus").startswith("ERROR")
