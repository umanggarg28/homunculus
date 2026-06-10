"""Harness-owned task closure (Pi guard-layer pattern).

Two invariants, both born from live failures on the daily-LeetCode task:

1. complete_task is GATED on the task's success criteria. The model
   closed a task with "could not fetch problem ...; task marked
   complete" without ever calling notify() — the user received nothing
   but the run was recorded as a success. Now the guard refuses the
   close until the criteria pass on notify texts that actually went out.

2. A criteria-satisfying run that's missing only the lifecycle call is
   completed BY THE HARNESS, not re-fired. notify() sends immediately,
   so re-firing a "silent drop" whose delivery already happened sent
   the user the same content twice (observed: six duplicate sends in
   one afternoon, 2026-06-08).

The blocked-complete_task result MUST start with "ERROR": core.py's
terminal-tool accounting counts any non-ERROR result from complete_task
as a successful close and exits the loop early.
"""

# heartbeat.py imports `from tools.notify import _send_to_telegram`; the
# conftest stubs `tools` as a flat module, so add the subpackage stub first.
import sys
import types

if "tools.notify" not in sys.modules:
    _stub = types.ModuleType("tools.notify")
    # Mirror test_autonomous_fallback_notify's stub shape — whichever
    # test module is imported first installs the stub for everyone.
    _telegram_calls: list[str] = []
    _stub._send_to_telegram = lambda text: _telegram_calls.append(text) or None
    _stub._telegram_calls = _telegram_calls
    sys.modules["tools.notify"] = _stub

from heartbeat import TaskGuard  # noqa: E402


CRITERIA = [
    {"type": "notify_called"},
    {"type": "notify_min_chars", "n": 50},
    {"type": "notify_has_code"},
]

GOOD_NOTIFY = (
    "Daily problem: Two Sum — find indices of two numbers adding to target.\n"
    "```python\ndef two_sum(nums, target): ...\n```"
)


def test_complete_task_blocked_when_notify_never_called():
    guard = TaskGuard({"t1": CRITERIA})
    result = guard.on_tool_call("complete_task", {"task_id": "t1"})
    assert result is not None
    assert result.startswith("ERROR")          # terminal accounting contract
    assert "notify() was never called" in result
    assert "record_failure" in result          # tells the model the way out
    # The task must still be reported as unfinished.
    assert guard.expected_remaining() == ["t1"]


def test_complete_task_allowed_after_satisfying_notify():
    guard = TaskGuard({"t1": CRITERIA})
    assert guard.on_tool_call("notify", {"text": GOOD_NOTIFY}) is None
    assert guard.on_tool_call("complete_task", {"task_id": "t1"}) is None
    assert guard.expected_remaining() == []


def test_complete_task_allowed_for_task_without_criteria():
    guard = TaskGuard({"t1": []})
    assert guard.on_tool_call("complete_task", {"task_id": "t1"}) is None
    assert guard.expected_remaining() == []


def test_complete_task_blocked_when_notify_was_itself_blocked():
    """A notify that failed the criteria was never sent — it must not
    count toward completing the task."""
    guard = TaskGuard({"t1": CRITERIA})
    blocked = guard.on_tool_call("notify", {"text": "too short"})
    assert blocked is not None and "BLOCKED" in blocked
    result = guard.on_tool_call("complete_task", {"task_id": "t1"})
    assert result is not None and result.startswith("ERROR")


def test_all_lifecycle_tools_count_as_explicit_close():
    """cancel_task / continue_task / record_failure all close the agent's
    responsibility — the post-tick check must not double-record a partial
    on a task the agent already explicitly closed."""
    for tool in ("record_failure", "cancel_task", "continue_task"):
        guard = TaskGuard({"t1": CRITERIA})
        guard.on_tool_call(tool, {"task_id": "t1", "reason": "x"})
        assert guard.expected_remaining() == [], tool


def test_criteria_failures_lists_unmet_criteria_per_task():
    guard = TaskGuard({"t1": CRITERIA, "t2": []})
    assert guard.criteria_failures("t2") == []          # no criteria → pass
    failures = guard.criteria_failures("t1")
    assert any("never called" in f for f in failures)
    guard.on_tool_call("notify", {"text": GOOD_NOTIFY})
    assert guard.criteria_failures("t1") == []
