"""Phase 2 — commitments: inferred check-in tasks the agent records to follow
up proactively (OpenClaw pattern), stored as source='inferred' tasks."""

from __future__ import annotations

from datetime import datetime

from homunculus.tasks import TaskStore
from tests.conftest import load_real_tool_submodule


def _freeze_now(monkeypatch, sched, iso: str) -> None:
    """Pin sched's view of 'now' so fixture dates (2026-07-04 etc.) stay
    in the future relative to it, regardless of when the suite actually
    runs — record_commitment now rejects a past event_at, so these
    fixtures need a frozen clock rather than the real one."""
    monkeypatch.setattr(sched, "now_user_naive", lambda: datetime.fromisoformat(iso))


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
    _freeze_now(monkeypatch, sched, "2026-07-01T00:00:00")
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
    _freeze_now(monkeypatch, sched, "2026-07-01T00:00:00")
    # Interview at 3pm → event_check_in fires ~2h before (1pm), never AT 3pm.
    sched.record_commitment("wish Umang luck", "2026-07-04T15:00:00", "event_check_in")
    due = sched._task_store().list("all")[0]["due_at"]
    assert due.startswith("2026-07-04T13:00")


def test_deadline_check_fires_a_day_before(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMUNCULUS_TASKS_DIR", str(tmp_path))
    sched = load_real_tool_submodule("scheduling")
    _freeze_now(monkeypatch, sched, "2026-07-01T00:00:00")
    sched.record_commitment("submit taxes", "2026-07-05T17:00:00", "deadline_check")
    due = sched._task_store().list("all")[0]["due_at"]
    assert due.startswith("2026-07-04T17:00")


def test_lead_hours_accepts_fractional_hours_for_a_just_before_reminder(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMUNCULUS_TASKS_DIR", str(tmp_path))
    sched = load_real_tool_submodule("scheduling")
    _freeze_now(monkeypatch, sched, "2026-07-01T00:00:00")
    # 15 min before a 14:00 event → 13:45, not truncated to the top of the hour.
    sched.record_commitment(
        "Standup (15 min)", "2026-07-04T14:00:00", "event_check_in", lead_hours=0.25,
    )
    due = sched._task_store().list("all")[0]["due_at"]
    assert due.startswith("2026-07-04T13:45")


def test_distinct_titles_record_two_reminders_for_the_same_event(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMUNCULUS_TASKS_DIR", str(tmp_path))
    sched = load_real_tool_submodule("scheduling")
    _freeze_now(monkeypatch, sched, "2026-07-01T00:00:00")
    sched.record_commitment(
        "Standup (1 day)", "2026-07-04T14:00:00", "event_check_in", lead_hours=24,
    )
    sched.record_commitment(
        "Standup (15 min)", "2026-07-04T14:00:00", "event_check_in", lead_hours=0.25,
    )
    tasks = sched._task_store().list("all")
    assert len(tasks) == 2
    dues = sorted(t["due_at"] for t in tasks)
    assert dues[0].startswith("2026-07-03T14:00")
    assert dues[1].startswith("2026-07-04T13:45")


def test_record_commitment_rejects_past_event_at(tmp_path, monkeypatch):
    """Observed live: gmail_search came back UNAVAILABLE and the model
    fabricated a commitment for an event dated the day before 'now',
    which would have fired a nonsense notify on the very next tick.
    A past event_at is never legitimate — reject it structurally."""
    monkeypatch.setenv("HOMUNCULUS_TASKS_DIR", str(tmp_path))
    sched = load_real_tool_submodule("scheduling")
    out = sched.record_commitment(
        "should never fire test", "2020-01-01T00:00:00", "event_check_in",
    )
    assert out.startswith("ERROR")
    assert sched._task_store().list("all") == []


def test_record_commitment_allows_event_at_within_the_grace_window(tmp_path, monkeypatch):
    """A few minutes of clock/timezone slop shouldn't reject a
    genuinely-now event — only clearly-past ones."""
    monkeypatch.setenv("HOMUNCULUS_TASKS_DIR", str(tmp_path))
    sched = load_real_tool_submodule("scheduling")
    from datetime import timedelta
    from homunculus.user_tz import now_user_naive
    soon = (now_user_naive() + timedelta(minutes=5)).isoformat(timespec="seconds")
    out = sched.record_commitment("standup", soon, "event_check_in")
    assert out.startswith("Recorded commitment")


def test_record_commitment_requires_event_at(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMUNCULUS_TASKS_DIR", str(tmp_path))
    sched = load_real_tool_submodule("scheduling")
    assert sched.record_commitment("something", "", "open_loop").startswith("ERROR")


def test_record_commitment_rejects_bad_kind(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMUNCULUS_TASKS_DIR", str(tmp_path))
    sched = load_real_tool_submodule("scheduling")
    assert sched.record_commitment("x", "2026-07-04T18:00:00", "bogus").startswith("ERROR")


def test_a_rephrased_commitment_is_not_a_second_reminder(tmp_path, monkeypatch):
    """One event described twice in different words is one commitment.

    Title dedup is deliberate — it is what lets an event carry both a
    day-before and a 15-min-before check-in — but it keys on text the model
    wrote, so a reword reads as a new commitment. The derived check-in time
    is the harness's, and two live check-ins of one kind firing at the same
    instant are redundant whatever they are called.
    """
    monkeypatch.setenv("HOMUNCULUS_TASKS_DIR", str(tmp_path))
    sched = load_real_tool_submodule("scheduling")
    _freeze_now(monkeypatch, sched, "2026-08-25T00:00:00")
    first = sched.record_commitment(
        "Next Steps call with Acme", "2026-08-26T15:00:00", "event_check_in",
    )
    second = sched.record_commitment(
        "Next Steps with Acme", "2026-08-26T15:00:00", "event_check_in",
    )
    assert "Recorded commitment" in first
    assert "Recorded commitment" not in second, second
    active = [t for t in sched._task_store().list("all") if t["status"] == "active"]
    assert len(active) == 1, [t["title"] for t in active]


def test_dedup_ignores_a_cancelled_commitment(tmp_path, monkeypatch):
    """Dedup stops redundant live reminders; it must not make a mistake
    permanent. A cancelled check-in is not going to fire."""
    monkeypatch.setenv("HOMUNCULUS_TASKS_DIR", str(tmp_path))
    sched = load_real_tool_submodule("scheduling")
    _freeze_now(monkeypatch, sched, "2026-08-25T00:00:00")
    sched.record_commitment("Call with Acme", "2026-08-26T15:00:00", "event_check_in")
    store = sched._task_store()
    store.cancel(store.list("all")[0]["id"], "no longer needed")
    out = sched.record_commitment("Acme sync", "2026-08-26T15:00:00", "event_check_in")
    assert "Recorded commitment" in out, out
