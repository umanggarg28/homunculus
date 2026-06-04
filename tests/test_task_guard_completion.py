"""Item 4 of the robustness plan — TaskGuard completion tracking.

Extends TaskGuard with a `_completed_tasks` set populated when the agent
calls complete_task or record_failure. Exposes `expected_remaining()` so
the heartbeat tick can distinguish:

  silent drop  → agent never touched the task (the bug we're hunting)
  explicit fail → agent called record_failure with a reason

The distinction matters because:
- silent drops need the post-success check to clean up the executing flag
  AND report a soft failure
- explicit fails are already in the task's last_runs with the agent's
  reason; the post-success check should respect that and not double-log
"""

# heartbeat.py imports `from tools.notify import _send_to_telegram`. The
# conftest stubs `tools` as a flat module without subpackages, so we add
# a tiny `tools.notify` stub here before importing heartbeat.
import sys
import types

if "tools.notify" not in sys.modules:
    _stub = types.ModuleType("tools.notify")
    _stub._send_to_telegram = lambda *_a, **_kw: None
    sys.modules["tools.notify"] = _stub

from heartbeat import TaskGuard  # noqa: E402


def test_expected_remaining_initially_lists_all_due_tasks():
    """Before any tool calls, every task with criteria is unmet."""
    guard = TaskGuard({
        "task-a": [{"type": "notify_called"}],
        "task-b": [{"type": "notify_called"}],
    })
    assert sorted(guard.expected_remaining()) == ["task-a", "task-b"]


def test_complete_task_marks_task_as_done():
    """A successful complete_task call removes the task from remaining."""
    guard = TaskGuard({
        "task-a": [],   # no criteria → complete_task passes through
        "task-b": [],
    })
    # Simulate the on_tool_call hook firing for complete_task
    guard.on_tool_call("complete_task", {"task_id": "task-a", "result": "delivered"})
    assert guard.expected_remaining() == ["task-b"]
    guard.on_tool_call("complete_task", {"task_id": "task-b", "result": "delivered"})
    assert guard.expected_remaining() == []


def test_record_failure_also_marks_task_as_handled():
    """An explicit record_failure call means the agent took responsibility —
    don't surface it again as a silent drop."""
    guard = TaskGuard({
        "task-a": [],
        "task-b": [],
    })
    guard.on_tool_call("record_failure", {"task_id": "task-a", "reason": "no data"})
    assert guard.expected_remaining() == ["task-b"]


def test_complete_task_blocked_by_criteria_does_NOT_mark_done():
    """If criteria fail, the agent gets BLOCKED and the task is NOT marked
    complete. The agent must retry."""
    guard = TaskGuard({
        "task-a": [{"type": "notify_called"}],
    })
    # No notify() was called yet, so the criteria check fails.
    blocked = guard.on_tool_call("complete_task", {"task_id": "task-a", "result": "x"})
    # Hook returns a BLOCKED message
    assert blocked is not None
    assert "BLOCKED" in blocked
    # And the task is STILL in remaining — agent must fix and retry
    assert guard.expected_remaining() == ["task-a"]


def test_complete_task_blocked_then_retry_succeeds():
    """The full retry pattern: notify, complete_task is blocked, fix the
    issue, complete_task succeeds, task moves to completed."""
    guard = TaskGuard({
        "task-a": [{"type": "notify_min_chars", "n": 50}],
    })
    # First notify is too short.
    guard.on_tool_call("notify", {"text": "short"})
    blocked = guard.on_tool_call("complete_task", {"task_id": "task-a", "result": "x"})
    assert blocked is not None and "BLOCKED" in blocked
    assert guard.expected_remaining() == ["task-a"]

    # Retry with a longer notify and complete_task again.
    guard.on_tool_call("notify", {"text": "x" * 60})
    result = guard.on_tool_call("complete_task", {"task_id": "task-a", "result": "ok"})
    assert result is None  # passed through cleanly
    assert guard.expected_remaining() == []


def test_silent_drop_is_visible_after_loop_returns():
    """If the agent's loop returns without any complete_task or
    record_failure, every due task remains in expected_remaining() —
    this is the signal the heartbeat tick uses to clean up."""
    guard = TaskGuard({
        "task-a": [],
        "task-b": [],
    })
    # Loop did some other tools but never closed out either task.
    guard.on_tool_call("read_file", {"path": "x.md"})
    guard.on_tool_call("web_search", {"query": "anything"})
    assert sorted(guard.expected_remaining()) == ["task-a", "task-b"]
