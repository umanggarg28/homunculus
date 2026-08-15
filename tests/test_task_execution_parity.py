"""Run-now and the scheduled heartbeat tick settle a task through ONE shared
core, so they cannot drift.

Both paths build their guard with `build_task_guard`, plan with
`prepare_task_run`, and close out with `settle_task_failure` /
`settle_task_outcome`. These tests pin that shared core's behavior — the
branches where the two paths used to disagree (auto-complete on a delivered
silent drop, partial on no delivery, the escalation-notify policy).
"""

from __future__ import annotations

import importlib
import sys
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path

if "homunculus.tools.notify" not in sys.modules:
    _stub = types.ModuleType("homunculus.tools.notify")
    _stub._send_to_telegram = lambda *_a, **_kw: None
    sys.modules["homunculus.tools.notify"] = _stub


def _store(tmp_path: Path):
    sys.modules.pop("homunculus.tasks", None)
    return importlib.import_module("homunculus.tasks").TaskStore(tmp_path)


def _overdue() -> str:
    return (datetime.now() - timedelta(minutes=5)).isoformat(timespec="seconds")


# ── build_task_guard: criteria + delivered ledger ────────────────────────

def test_build_task_guard_includes_delivered_ledger(tmp_path):
    """The guard carries the task's delivered keys so notify_unique works the
    same on run-now as on a scheduled tick (run-now used to omit this)."""
    from homunculus.heartbeat import build_task_guard

    task = {
        "id": "t1",
        "success_criteria": [{"type": "notify_called"}],
        "delivered": [{"key": "already-sent"}],
    }
    guard = build_task_guard(task)
    assert guard._delivered.get("t1") == {"already-sent"}
    assert guard._criteria.get("t1") == [{"type": "notify_called"}]


# ── settle_task_outcome: the branches that used to diverge ────────────────

def test_silent_drop_with_satisfied_criteria_auto_completes(tmp_path):
    """Delivered but `complete_task` omitted + every criterion passed → the
    harness completes it (due_at advances). This is the run-now bug that
    started the audit; both paths now share this branch."""
    from homunculus.heartbeat import build_task_guard, settle_task_outcome

    store = _store(tmp_path)
    task = store.create(
        title="brief", due_at=_overdue(), recurrence="daily",
        success_criteria=[{"type": "notify_called"}],
    )
    due_at_before = task["due_at"]

    guard = build_task_guard(task)
    guard.on_tool_call("notify", {"text": "Morning — real delivered content."})

    settle_task_outcome(
        None, store, task, guard,
        due_at_before=due_at_before,
        started=datetime.now(), started_utc=datetime.now(UTC),
        fire_escalation_notify=False,
    )
    updated = store.get(task["id"])
    # Completed → due_at advanced by a full recurrence (~a day), not a retry,
    # and it's not counted as a partial.
    advance = (
        datetime.fromisoformat(updated["due_at"]) - datetime.fromisoformat(due_at_before)
    ).total_seconds()
    assert advance >= 3600
    assert int(updated.get("consecutive_partials", 0)) == 0


def test_silent_drop_without_delivery_marks_partial(tmp_path):
    """No delivery that satisfies the criteria → partial (due_at unchanged so
    the next tick resumes), not a spurious complete."""
    from homunculus.heartbeat import build_task_guard, settle_task_outcome

    store = _store(tmp_path)
    task = store.create(
        title="brief", due_at=_overdue(), recurrence="daily",
        success_criteria=[{"type": "notify_min_chars", "n": 50}],
    )
    due_at_before = task["due_at"]

    guard = build_task_guard(task)  # no notify → criterion fails

    settle_task_outcome(
        None, store, task, guard,
        due_at_before=due_at_before,
        started=datetime.now(), started_utc=datetime.now(UTC),
        fire_escalation_notify=False,
    )
    updated = store.get(task["id"])
    # Partial reschedules a short retry (~10-15 min), NOT a full recurrence,
    # and is counted as a partial so the next tick resumes.
    advance = (
        datetime.fromisoformat(updated["due_at"]) - datetime.fromisoformat(due_at_before)
    ).total_seconds()
    assert 0 < advance < 3600
    assert int(updated.get("consecutive_partials", 0)) >= 1


def test_escalation_notify_suppressed_for_run_now(tmp_path, monkeypatch):
    """fire_escalation_notify=False (run-now) must not push the unattended
    'I tried this multiple times' message — the operator is watching."""
    from homunculus import heartbeat
    from homunculus.heartbeat import build_task_guard, settle_task_outcome

    calls: list = []
    monkeypatch.setattr(heartbeat, "deliver", lambda text: calls.append(text))

    store = _store(tmp_path)
    task = store.create(
        title="brief", due_at=_overdue(), recurrence="daily", notify=True,
        success_criteria=[{"type": "notify_min_chars", "n": 50}],
    )
    # Drive it to an escalated partial: repeated no-delivery runs.
    for _ in range(4):
        guard = build_task_guard(task)
        settle_task_outcome(
            None, store, task, guard,
            due_at_before=task["due_at"],
            started=datetime.now(), started_utc=datetime.now(UTC),
            fire_escalation_notify=False,
        )
        task = store.get(task["id"])
    assert calls == []  # never escalated to the user on a watched run-now


def test_escalation_notify_fires_for_scheduled_when_escalated(tmp_path, monkeypatch):
    """The scheduled path (fire_escalation_notify=True) still pushes once the
    partial escalates — proving suppression is the parameter, not a global off."""
    from homunculus import heartbeat
    from homunculus.heartbeat import build_task_guard, settle_task_outcome

    calls: list = []
    monkeypatch.setattr(heartbeat, "deliver", lambda text: calls.append(text))

    store = _store(tmp_path)
    task = store.create(
        title="brief", due_at=_overdue(), recurrence="daily", notify=True,
        success_criteria=[{"type": "notify_min_chars", "n": 50}],
    )
    for _ in range(4):
        guard = build_task_guard(task)
        settle_task_outcome(
            None, store, task, guard,
            due_at_before=task["due_at"],
            started=datetime.now(), started_utc=datetime.now(UTC),
            fire_escalation_notify=True,
        )
        task = store.get(task["id"])
    assert calls, "scheduled path should escalate to the user after repeated failures"


# ── a forced run completes a task that was never due ──────────────────────


def test_forced_run_success_still_gets_its_artifacts(tmp_path):
    """run-now completes a task that is not due, so `due_at` deliberately does
    not advance and the "due_at moved" branch is skipped. The run still
    delivered, and without its tool_trace it scores as a contract violation
    with no model, cost or skill version recorded — an unattributed success
    silently drags down the scorecard it belongs to.
    """
    from homunculus.heartbeat import build_task_guard, settle_task_outcome

    store = _store(tmp_path)
    future = (datetime.now() + timedelta(days=6)).isoformat(timespec="seconds")
    task = store.create(
        title="github health", due_at=future, recurrence="weekly",
        success_criteria=[{"type": "notify_called"}],
    )
    due_at_before = task["due_at"]

    guard = build_task_guard(task)
    guard.on_tool_call("notify", {"text": "Quiet week — totals: 1 star, 9 followers."})
    guard.on_tool_call("complete_task", {"task_id": task["id"]})
    # The tool layer appends the success run from inside the loop. `complete`
    # advances due_at only past `now`, so a task already due in the future
    # keeps its date — which is what skips the branch above.
    store.complete(task["id"], "Quiet week — no change.")

    settle_task_outcome(
        None, store, task, guard,
        due_at_before=due_at_before,
        started=datetime.now(), started_utc=datetime.now(UTC),
        fire_escalation_notify=False,
    )

    updated = store.get(task["id"])
    assert updated["due_at"] == due_at_before, "a forced run must not skip a real cycle"
    last = (updated.get("last_runs") or [])[-1]
    assert last["status"] == "success"
    assert last.get("tool_trace"), "no trace → scored as a contract violation"
    assert "notify" in last["tool_trace"]


def test_forced_run_failure_is_still_recorded_as_a_failure(tmp_path):
    """The success branch must not swallow the failure case that shared it."""
    from homunculus.heartbeat import build_task_guard, settle_task_outcome

    store = _store(tmp_path)
    future = (datetime.now() + timedelta(days=6)).isoformat(timespec="seconds")
    task = store.create(
        title="github health", due_at=future, recurrence="weekly",
        success_criteria=[{"type": "notify_called"}],
    )
    guard = build_task_guard(task)
    guard.on_tool_call("record_failure", {"task_id": task["id"], "reason": "api down"})
    store.record_failure(task["id"], "api down")

    settle_task_outcome(
        None, store, task, guard,
        due_at_before=task["due_at"],
        started=datetime.now(), started_utc=datetime.now(UTC),
        fire_escalation_notify=False,
    )
    last = (store.get(task["id"]).get("last_runs") or [])[-1]
    assert last["status"] == "failure"
