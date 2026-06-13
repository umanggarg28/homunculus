"""week_in_review — the agent's deterministic 7-day self-report.

Same contract as task_health_summary: the harness computes the numbers,
the model only formats them. These tests pin that the tool returns
well-formed JSON whose figures come straight from stats.summarize_events
(shared with the dashboard) and from the task store's run history,
windowed to the last 7 days.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from tests.conftest import load_real_tool_submodule

# report.py imports from .scheduling which imports the real tasks module;
# load it as a real submodule under the canonical name.
load_real_tool_submodule("_state")
load_real_tool_submodule("scheduling")
_report = load_real_tool_submodule("report")


@pytest.fixture()
def wired(tmp_path, monkeypatch):
    """Point the task store and event log at tmp_path."""
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    monkeypatch.setenv("HOMUNCULUS_TASKS_DIR", str(tasks_dir))
    events = tmp_path / "_events.jsonl"
    monkeypatch.setenv("HOMUNCULUS_EVENTS_PATH", str(events))
    monkeypatch.setenv("HOMUNCULUS_DAILY_BUDGET_USD", "0.17")
    return tasks_dir, events


def test_empty_world_returns_valid_zeroed_report(wired):
    out = json.loads(_report.week_in_review())
    assert out["cost"]["total_cents"] == 0.0
    # 0.17 USD/day * 100 cents * 7 days = 119 cents
    assert out["cost"]["weekly_budget_cents"] == pytest.approx(119.0)
    assert out["activity"]["events"] == 0
    assert out["tasks"] == []
    assert "from" in out["window"] and "to" in out["window"]


def test_activity_counts_flow_from_event_log(wired):
    _tasks_dir, events = wired
    recent = (datetime.now() - timedelta(hours=2)).astimezone().isoformat(timespec="seconds")
    events.write_text("\n".join(json.dumps(r) for r in [
        {"ts": recent, "event": "tool_call", "name": "notify"},
        {"ts": recent, "event": "tool_call", "name": "complete_task"},
        {"ts": recent, "event": "tool_blocked", "name": "shell_exec"},
    ]) + "\n", encoding="utf-8")
    out = json.loads(_report.week_in_review())
    assert out["activity"]["notifications_sent"] == 1
    assert out["activity"]["tasks_completed"] == 1
    assert out["activity"]["calls_blocked_by_guards"] == 1


def test_task_outcomes_windowed_to_last_7_days(wired):
    tasks_dir, _events = wired
    # Write tasks.json directly: no public API backdates a run's ts, and
    # report.py only reads the last_runs list off each task record.
    fresh = (datetime.now() - timedelta(days=2)).isoformat(timespec="seconds")
    stale = (datetime.now() - timedelta(days=10)).isoformat(timespec="seconds")
    (tasks_dir / "tasks.json").write_text(json.dumps([{
        "id": "watch-something",
        "title": "Watch something",
        "status": "active",
        "last_runs": [
            {"ts": stale, "status": "failure", "result": "boom"},
            {"ts": fresh, "status": "success", "result": "ok"},
        ],
    }]), encoding="utf-8")

    rows = {r["id"]: r for r in json.loads(_report.week_in_review())["tasks"]}
    row = rows["watch-something"]
    assert row["runs_this_week"] == 1
    assert row["outcomes"] == {"success": 1}
