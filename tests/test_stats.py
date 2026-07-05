"""stats.py — shared event-log aggregation.

One counting implementation feeds both /api/stats/today and the agent's
week_in_review tool. These tests pin the counting semantics so a future
edit can't silently make the dashboard and the agent's self-report
disagree.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, UTC
from zoneinfo import ZoneInfo

import pytest

from homunculus import stats


def _write_events(path, records):
    path.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8"
    )


def _ts(hours_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat(
        timespec="seconds"
    )


@pytest.fixture()
def events_file(tmp_path):
    return tmp_path / "_events.jsonl"


def test_counts_each_event_kind(events_file):
    _write_events(events_file, [
        {"ts": _ts(1), "event": "tool_call", "name": "notify"},
        {"ts": _ts(1), "event": "tool_call", "name": "complete_task"},
        {"ts": _ts(1), "event": "tool_call", "name": "record_failure"},
        {"ts": _ts(1), "event": "tool_blocked", "name": "shell_exec"},
        {"ts": _ts(1), "event": "memory_write"},
        {"ts": _ts(1), "event": "memory_forget"},
        {"ts": _ts(1), "event": "llm_call", "model": "x:free",
         "input_tokens": 100, "output_tokens": 50, "cached_tokens": 10},
    ])
    since = datetime.now(UTC) - timedelta(days=1)
    s = stats.summarize_events(since, path=events_file)
    assert s["events"] == 7
    assert s["notifies"] == 1
    assert s["tasks_fired"] == 1
    assert s["task_failures"] == 1
    assert s["blocked"] == 1
    assert s["memory_writes"] == 1
    assert s["memory_forgets"] == 1
    assert s["llm_calls"] == 1
    assert s["input_tokens"] == 100
    assert s["output_tokens"] == 50
    assert s["cached_tokens"] == 10
    assert s["unique_tools"] == ["complete_task", "notify", "record_failure"]


def test_window_cutoff_excludes_old_events(events_file):
    _write_events(events_file, [
        {"ts": _ts(50), "event": "tool_call", "name": "notify"},
        {"ts": _ts(1), "event": "tool_call", "name": "notify"},
    ])
    since = datetime.now(UTC) - timedelta(hours=24)
    s = stats.summarize_events(since, path=events_file)
    assert s["notifies"] == 1


def test_malformed_lines_and_bad_timestamps_skipped(events_file):
    _write_events(events_file, [
        {"ts": _ts(1), "event": "memory_write"},
    ])
    with events_file.open("a") as f:
        f.write("{not json\n")
        f.write(json.dumps({"ts": "garbage", "event": "memory_write"}) + "\n")
        f.write(json.dumps({"ts": _ts(0.5), "event": "memory_write"}) + "\n")
    since = datetime.now(UTC) - timedelta(days=1)
    s = stats.summarize_events(since, path=events_file)
    assert s["memory_writes"] == 2


def test_missing_file_returns_zeroes(tmp_path):
    since = datetime.now(UTC) - timedelta(days=1)
    s = stats.summarize_events(since, path=tmp_path / "nope.jsonl")
    assert s["events"] == 0
    assert s["cost_cents"] == 0.0


def test_naive_since_rejected(events_file):
    with pytest.raises(ValueError):
        stats.summarize_events(datetime.now(), path=events_file)


# ---- cost ---------------------------------------------------------------


def test_free_models_cost_zero_and_unknown_paid_costs_conservative():
    """CONTRACT CHANGE (was: unknown models cost 0). The UI number must be
    the number the budget enforcer counts — llm.py costs an unlisted paid
    model at conservative default rates (fail closed on cost), so stats
    must too. An unknown paid model showing ¢0.0 in the UI while the
    ceiling accrues real spend is exactly the drift this delegation kills.
    """
    assert stats.model_cost_cents("anything:free", 10_000, 10_000, 0) == 0.0
    assert stats.model_cost_cents("", 10_000, 10_000, 0) == 0.0
    assert stats.model_cost_cents("unknown/model", 10_000, 10_000, 0) > 0.0


def test_paid_model_cost_includes_cached_discount():
    # gemini-2.5-flash: $0.15/M in, $0.60/M out; cached input billed 10%.
    cost = stats.model_cost_cents("gemini-2.5-flash", 1_000_000, 0, 1_000_000)
    assert cost == pytest.approx(0.15 * 100 * 0.1)


def test_cost_bucketed_per_day_in_given_timezone(events_file):
    # An event at 22:00 UTC is the NEXT calendar day in IST (+05:30).
    late_utc = datetime.now(UTC).replace(
        hour=22, minute=0, second=0, microsecond=0
    ) - timedelta(days=1)
    _write_events(events_file, [
        {"ts": late_utc.isoformat(timespec="seconds"), "event": "llm_call",
         "model": "gemini-2.5-flash", "input_tokens": 1_000_000,
         "output_tokens": 0, "cached_tokens": 0},
    ])
    since = datetime.now(UTC) - timedelta(days=3)
    ist = ZoneInfo("Asia/Kolkata")
    s = stats.summarize_events(since, path=events_file, tz=ist)
    expected_day = late_utc.astimezone(ist).date().isoformat()
    assert list(s["cost_per_day"].keys()) == [expected_day]
    assert s["cost_cents"] == pytest.approx(15.0)  # 1M tok * $0.15/M = 15¢


def test_keepalive_pings_are_not_activity(tmp_path):
    """service_ping fires every few minutes from every container even
    when the agent does nothing — counting it as activity overstated a
    quiet day ~3x (2026-07-05: 278 of 403 'events' were pings)."""
    p = tmp_path / "events.jsonl"
    now = datetime.now(UTC)
    lines = []
    for i in range(10):
        lines.append(json.dumps({"ts": (now - timedelta(minutes=i)).isoformat(), "event": "service_ping", "service": "web"}))
    lines.append(json.dumps({"ts": now.isoformat(), "event": "tool_call", "name": "notify"}))
    p.write_text("\n".join(lines) + "\n")

    s = stats.summarize_events(now - timedelta(hours=1), path=p)
    assert s["events"] == 1, "only the tool_call is activity"
    assert s["notifies"] == 1
