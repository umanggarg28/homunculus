"""TaskGuard — sentinel-leak gate on notify() and required-calls gate on
complete_task().

Regression for the 2026-07-02 morning brief: the model skipped
news_headlines entirely and delivered "Top headlines:\n- NEWS_UNAVAILABLE" —
pasting the tool-failure sentinel as if it were the tool's output. Every
success criterion passed vacuously (notify_called, min_chars, contains;
links_grounded had zero links to check), and requires_tools only gated tool
EXISTENCE, so the run settled as a success.

Two deterministic checks close the gap:
- notify() refuses text carrying a tool-failure sentinel (NEWS_UNAVAILABLE /
  WEATHER UNAVAILABLE) — those tokens are tool→model signals, never content.
- complete_task() refuses to close a task until every tool in the skill's
  requires_tools was at least ATTEMPTED this run. A failed attempt counts:
  sections still degrade gracefully when a source errors; only skipping the
  source is blocked.
"""

import sys
import types

if "homunculus.tools.notify" not in sys.modules:
    _stub = types.ModuleType("homunculus.tools.notify")
    _stub._send_to_telegram = lambda *_a, **_kw: None
    sys.modules["homunculus.tools.notify"] = _stub

from homunculus.heartbeat import TaskGuard, _plan_tick, build_task_guard  # noqa: E402


# ---------------------------------------------------------------- sentinel


def test_notify_with_news_sentinel_is_blocked():
    guard = TaskGuard({"brief": []})
    blocked = guard.on_tool_call(
        "notify", {"text": "Morning!\n\nTop headlines:\n- NEWS_UNAVAILABLE"}
    )
    assert blocked is not None and "BLOCKED" in blocked
    assert "NEWS_UNAVAILABLE" in blocked
    assert guard.combined_notify_text() == ""  # nothing recorded as sent


def test_notify_with_weather_sentinel_is_blocked():
    guard = TaskGuard({"brief": []})
    blocked = guard.on_tool_call(
        "notify", {"text": "WEATHER UNAVAILABLE: forecast request failed"}
    )
    assert blocked is not None and "WEATHER UNAVAILABLE" in blocked


def test_notify_without_sentinel_passes():
    guard = TaskGuard({"brief": []})
    assert guard.on_tool_call("notify", {"text": "Morning! All clear today."}) is None


# ---------------------------------------------------------- required calls


def test_complete_task_blocked_until_required_tools_attempted():
    guard = TaskGuard(
        {"brief": []},
        required_calls_by_task={"brief": ["get_weather", "news_headlines"]},
    )
    guard.on_tool_call("get_weather", {})
    blocked = guard.on_tool_call("complete_task", {"task_id": "brief", "result": "x"})
    assert blocked is not None and blocked.startswith("ERROR")
    assert "news_headlines" in blocked
    assert "get_weather" not in blocked  # already attempted — not listed
    assert guard.expected_remaining() == ["brief"]

    guard.on_tool_call("news_headlines", {"limit": 5})
    assert guard.on_tool_call("complete_task", {"task_id": "brief", "result": "x"}) is None
    assert guard.expected_remaining() == []


def test_failed_attempt_still_counts_as_exercised():
    """Graceful degradation stays intact: a source that ERRORS was still
    consulted, so the task may complete with that section omitted."""
    guard = TaskGuard(
        {"brief": []},
        required_calls_by_task={"brief": ["news_headlines"]},
    )
    guard.on_tool_call("news_headlines", {})
    guard.observe_tool_result(
        "news_headlines", "NEWS_UNAVAILABLE: every configured feed failed to fetch."
    )
    assert guard.on_tool_call("complete_task", {"task_id": "brief", "result": "x"}) is None


def test_skill_listing_lifecycle_tools_does_not_deadlock():
    """Some skills declare notify/complete_task in requires_tools. The
    in-flight complete_task is already in the trace when the gate runs, so
    the only real requirement left is the data tool + notify."""
    guard = TaskGuard(
        {"lc": []},
        required_calls_by_task={"lc": ["leetcode_next_problem", "notify", "complete_task"]},
    )
    guard.on_tool_call("leetcode_next_problem", {})
    guard.on_tool_call("notify", {"text": "today's problem: Two Sum"})
    assert guard.on_tool_call("complete_task", {"task_id": "lc", "result": "x"}) is None


def test_tasks_without_required_calls_are_unaffected():
    guard = TaskGuard({"adhoc": []})
    assert guard.on_tool_call("complete_task", {"task_id": "adhoc", "result": "x"}) is None


# ------------------------------------------------------------- regression


def test_regression_2026_07_02_brief_without_news():
    """Replay of the live failure: task_health_summary → get_weather →
    notify with the sentinel pasted in → complete_task, news_headlines
    never called. Both gates must fire; the corrected flow must pass."""
    guard = TaskGuard(
        {"morning-brief": [
            {"type": "notify_called"},
            {"type": "notify_contains", "text": "Morning, Umang"},
            {"type": "notify_min_chars", "n": 120},
        ]},
        required_calls_by_task={
            "morning-brief": ["get_weather", "task_health_summary", "news_headlines"],
        },
    )
    guard.on_tool_call("task_health_summary", {})
    guard.on_tool_call("get_weather", {})

    delivered = (
        "Morning, Umang\n\nToday's commitments:\n- Quiz coach (due 20:00)\n"
        "- Apply for jobs (due 05:30 tomorrow)\n\n"
        "Weather: thunderstorm with slight hail, high 37°C, low 29°C.\n\n"
        "Top headlines:\n- NEWS_UNAVAILABLE"
    )
    blocked_notify = guard.on_tool_call("notify", {"text": delivered})
    assert blocked_notify is not None and "NEWS_UNAVAILABLE" in blocked_notify

    blocked_complete = guard.on_tool_call(
        "complete_task", {"task_id": "morning-brief", "result": "Morning brief delivered"}
    )
    assert blocked_complete is not None and "news_headlines" in blocked_complete

    # Corrected flow: consult the source, deliver real content, close.
    guard.on_tool_call("news_headlines", {"limit": 5})
    guard.observe_tool_result(
        "news_headlines", "- [Real headline](https://example.org/story)"
    )
    ok = guard.on_tool_call(
        "notify",
        {"text": delivered.replace(
            "- NEWS_UNAVAILABLE", "- [Real headline](https://example.org/story)"
        )},
    )
    assert ok is None
    assert guard.on_tool_call(
        "complete_task", {"task_id": "morning-brief", "result": "delivered"}
    ) is None


# ----------------------------------------------------------------- wiring


BRIEF_SKILL = """---
name: skill_mini_brief
description: Mini brief
type: skill
requires_tools:
  - get_weather
  - news_headlines
---

# Playbook
1. get_weather()
2. news_headlines(limit=5)
3. notify the result.
"""


def test_plan_tick_folds_required_tools_onto_task(tmp_path, monkeypatch):
    from homunculus import heartbeat
    (tmp_path / "skill_mini_brief.md").write_text(BRIEF_SKILL, encoding="utf-8")
    monkeypatch.setattr(
        heartbeat, "_known_tool_names",
        lambda: {"get_weather", "news_headlines", "notify"},
    )
    task = {"id": "t1", "title": "t1", "skill": "skill_mini_brief"}
    _plan_tick([task], tmp_path)
    assert task["required_tool_calls"] == ["get_weather", "news_headlines"]

    guard = build_task_guard(task)
    assert guard.missing_required_calls("t1") == ["get_weather", "news_headlines"]


def test_record_failure_blocked_when_run_already_succeeded():
    """Observed close-out mode (2026-07-05 morning-brief): deliver fine,
    then grab record_failure as a generic wrap-up tool — stamping a false
    failure on a delivered run. The guard has ground truth and refuses."""
    guard = TaskGuard(
        {"morning-brief": [{"type": "notify_called"}]},
        required_calls_by_task={"morning-brief": ["news_headlines"]},
    )
    guard.on_tool_call("news_headlines", {})
    guard.observe_tool_result("news_headlines", "- headline")
    assert guard.on_tool_call("notify", {"text": "Morning, Umang — brief…"}) is None

    verdict = guard.on_tool_call("record_failure", {"task_id": "morning-brief", "reason": "No further action required"})
    assert verdict is not None and verdict.startswith("ERROR")
    assert "complete_task" in verdict
    # The refused call must NOT count as a close-out.
    assert "morning-brief" not in guard._completed_tasks


def test_record_failure_allowed_when_criteria_unmet():
    """A genuine failure (never delivered) records normally."""
    guard = TaskGuard({"morning-brief": [{"type": "notify_called"}]})
    assert guard.on_tool_call("record_failure", {"task_id": "morning-brief", "reason": "source down"}) is None
    assert "morning-brief" in guard._completed_tasks


def test_record_failure_allowed_when_required_calls_missing():
    """Criteria met but a declared source was skipped: complete_task would
    be blocked, so record_failure must stay available — the model can
    never be refused by both gates at once."""
    guard = TaskGuard(
        {"t": [{"type": "notify_called"}]},
        required_calls_by_task={"t": ["news_headlines"]},
    )
    assert guard.on_tool_call("notify", {"text": "something"}) is None
    assert guard.on_tool_call("record_failure", {"task_id": "t", "reason": "skipped source"}) is None
