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

if "homunculus.tools.notify" not in sys.modules:
    _stub = types.ModuleType("homunculus.tools.notify")
    _stub._send_to_telegram = lambda *_a, **_kw: None
    sys.modules["homunculus.tools.notify"] = _stub

from homunculus.heartbeat import TaskGuard  # noqa: E402


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


def test_notify_blocked_when_criteria_fail_pre_send():
    """Criteria are now enforced at notify() call time, not at
    complete_task(). A too-short notify is REFUSED — the message never
    reaches the user as garbage, but also nothing is buffered to lose."""
    guard = TaskGuard({
        "task-a": [{"type": "notify_min_chars", "n": 50}],
    })
    blocked = guard.on_tool_call("notify", {"text": "short"})
    assert blocked is not None
    assert "BLOCKED" in blocked
    assert "notify text too short" in blocked


def test_notify_passes_when_criteria_satisfied():
    guard = TaskGuard({
        "task-a": [{"type": "notify_min_chars", "n": 5}],
    })
    out = guard.on_tool_call("notify", {"text": "long enough message"})
    assert out is None  # not blocked → MCP subprocess sends it for real


def test_notify_retry_after_block_succeeds():
    """The retry pattern: notify is too short → BLOCKED, agent retries
    with longer text → passes → complete_task closes the lifecycle."""
    guard = TaskGuard({
        "task-a": [{"type": "notify_min_chars", "n": 50}],
    })
    # First attempt: refused.
    blocked = guard.on_tool_call("notify", {"text": "short"})
    assert blocked is not None and "BLOCKED" in blocked
    # Retry with the right size.
    out = guard.on_tool_call("notify", {"text": "x" * 60})
    assert out is None  # delivered
    # complete_task is now just a lifecycle marker — it passes through.
    result = guard.on_tool_call("complete_task", {"task_id": "task-a", "result": "ok"})
    assert result is None
    assert guard.expected_remaining() == []


def test_complete_task_re_checks_criteria():
    """CONTRACT CHANGE (was: complete_task never blocks). The pure-marker
    design assumed the silent-drop fallback would catch an undelivered
    close — but a model-called complete_task CLOSES the task, so the
    fallback never sees it. Observed live 2026-06-11: the agent closed
    the LeetCode task with 'could not fetch problem ...; task marked
    complete' and the user received nothing, recorded as success.
    complete_task now re-checks the task's criteria and refuses to close
    an undelivered task. See test_harness_task_closure.py for the full
    matrix."""
    guard = TaskGuard({
        "task-a": [{"type": "notify_called"}],
    })
    result = guard.on_tool_call("complete_task", {"task_id": "task-a", "result": "x"})
    assert result is not None and result.startswith("ERROR")
    assert guard.expected_remaining() == ["task-a"]


def test_notify_must_satisfy_all_criteria_in_one_call():
    """Under the new synchronous-gate design, every notify call has to
    satisfy every criterion on its own — there's no buffer to combine
    with previous calls. This is a deliberate tightening: the buffered
    design failed silently when the agent skipped complete_task, so we
    trade per-call flexibility for guaranteed delivery."""
    guard = TaskGuard({
        "task-a": [
            {"type": "notify_min_chars", "n": 30},
            {"type": "notify_has_code"},
        ],
    })
    # Long enough but no code → blocked.
    blocked1 = guard.on_tool_call("notify", {"text": "x" * 40})
    assert blocked1 is not None and "no code block" in blocked1
    # Short with code → blocked on length.
    blocked2 = guard.on_tool_call("notify", {"text": "```py\nx\n```"})
    assert blocked2 is not None and "too short" in blocked2
    # One call with both → delivered.
    ok = guard.on_tool_call("notify", {"text": "x" * 40 + "\n```py\nprint('hi')\n```"})
    assert ok is None


def test_regression_silent_drop_after_notify_still_delivers():
    """REGRESSION: 2026-06-07 / 06-08 — user's daily LeetCode failed
    twice. Agent called notify() with real content, then wrote 'task
    complete' as prose without calling complete_task(). Under the old
    buffered design, the notify text was held in TaskGuard memory and
    DROPPED when the loop ended without complete_task — user got nothing.

    Under the new design, on_tool_call('notify', ...) returns None for
    a criteria-passing message, which means the MCP subprocess sends
    it for real immediately. No buffer to lose. The agent forgetting
    complete_task() is logged as a silent drop, but the delivery
    already happened.
    """
    guard = TaskGuard({
        "daily-leetcode": [
            {"type": "notify_called"},
            {"type": "notify_min_chars", "n": 200},
            {"type": "notify_has_code"},
        ],
    })
    # Agent calls notify with proper content.
    leetcode_msg = (
        "Problem: Best Time to Buy and Sell Stock III\n"
        "Approach: dynamic programming tracking buy/sell state for "
        "up to two transactions. " + "x" * 100 + "\n"
        "```python\ndef maxProfit(prices):\n    return 0\n```"
    )
    out = guard.on_tool_call("notify", {"text": leetcode_msg})
    assert out is None, (
        "notify with proper content must pass through — the MCP "
        "subprocess will send it. If this returns a string, the "
        "delivery is blocked."
    )
    # Agent skips complete_task and the loop ends. expected_remaining
    # still flags the silent drop so the heartbeat can mark partial —
    # but the user already received the LeetCode message.
    assert guard.expected_remaining() == ["daily-leetcode"]


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
