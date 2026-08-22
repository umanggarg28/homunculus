"""A task run should know how its own last few runs went.

Before this, a scheduled run started cold: it saw its scratchpad and the
delivery ledger, but nothing about outcomes. A source that had failed three
mornings running looked, to the model, like it was failing for the first
time — so the skill led with it again and the same section collapsed again.

The harness counts and the model judges. Handing the model raw run history to
tally itself is the antipattern `week_in_review` and `_format_due_tasks`
already exist to avoid.
"""

from __future__ import annotations

import sys
import types

if "homunculus.tools.notify" not in sys.modules:
    _stub = types.ModuleType("homunculus.tools.notify")
    _stub._send_to_telegram = lambda *_a, **_kw: None
    sys.modules["homunculus.tools.notify"] = _stub

from homunculus.heartbeat import (  # noqa: E402
    _RECENT_RUNS_WINDOW,
    _format_due_tasks,
    _recent_run_summary,
)


def _task(**over):
    base = {
        "id": "morning-brief",
        "title": "Morning brief",
        "due_at": "2026-08-23T10:00:00",
        "recurrence": "daily",
    }
    base.update(over)
    return base


def test_no_history_says_nothing():
    """A first run must not be given an empty ceremonial block."""
    assert _recent_run_summary(_task()) == ""
    assert _recent_run_summary(_task(last_runs=[])) == ""


def test_statuses_are_newest_first():
    task = _task(last_runs=[
        {"status": "success"}, {"status": "partial"}, {"status": "failure"},
    ])
    out = _recent_run_summary(task)
    assert "recent_runs (newest first): failure · partial · success" in out


def test_repeatedly_failing_source_is_named_with_its_count():
    task = _task(last_runs=[
        {"status": "partial", "failed_tools": ["get_weather"]},
        {"status": "partial", "failed_tools": ["get_weather"]},
        {"status": "success"},
    ])
    out = _recent_run_summary(task)
    assert "get_weather (failed 2 of last 3 runs)" in out
    # The instruction must not tell it to skip the source — a recovered
    # source has to be noticed.
    assert "still CALL them" in out


def test_a_healthy_history_names_no_failing_source():
    task = _task(last_runs=[{"status": "success"}, {"status": "success"}])
    out = _recent_run_summary(task)
    assert "recent_runs" in out
    assert "recently failing" not in out


def test_window_is_bounded():
    """Prompt budget is finite; old history is the reflection's job."""
    task = _task(last_runs=[{"status": "success"}] * 40)
    out = _recent_run_summary(task)
    assert out.count("·") == _RECENT_RUNS_WINDOW - 1


def test_it_reaches_the_prompt_the_model_actually_sees():
    """The unit above is worthless if the block never gets rendered."""
    task = _task(last_runs=[
        {"status": "partial", "failed_tools": ["gmail_search"]},
        {"status": "partial", "failed_tools": ["gmail_search"]},
    ])
    rendered = _format_due_tasks([task])
    assert "recent_runs (newest first): partial · partial" in rendered
    assert "gmail_search (failed 2 of last 2 runs)" in rendered


def test_ordering_puts_the_worst_source_first():
    task = _task(last_runs=[
        {"status": "partial", "failed_tools": ["gmail_search", "get_weather"]},
        {"status": "partial", "failed_tools": ["gmail_search"]},
        {"status": "partial", "failed_tools": ["gmail_search"]},
    ])
    out = _recent_run_summary(task)
    assert out.index("gmail_search") < out.index("get_weather")


def test_partial_closes_also_carry_evidence():
    """The commonest failure shape is a partial, not a recorded failure.

    A source outage usually ends in continue_task. Attributing the guard's
    observations only on the record_failure path meant a task could go
    partial for days with nothing recording WHICH source was down — so the
    summary above would carry statuses but never name the culprit, which is
    the half that changes what the next run does.

    Asserted structurally because the behavioural path needs a whole tick;
    what must hold is that the attribution is not nested under the
    failure-only branch.
    """
    from pathlib import Path

    src = (
        Path(__file__).resolve().parent.parent / "homunculus" / "heartbeat.py"
    ).read_text()
    evidence_at = src.index("tasks.attribute_failure_evidence_to_last_run(task_id, guard.failed_tools())")
    failure_branch = src.index('if last_status == "failure":\n                _rate_task_skill')
    assert evidence_at < failure_branch, (
        "failure evidence is attributed only on the record_failure path, so "
        "partial closes leave no record of which source broke"
    )
