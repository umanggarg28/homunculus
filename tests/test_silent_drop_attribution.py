"""Silent-drop settlement must attribute delivery evidence to the run.

Regression (live, 2026-07-04 09:05 tick): the LeetCode and GitHub tasks
both delivered fine, but the model ended each loop writing
`record_failure(...)` as PROSE instead of calling a tool — so both runs
settled through the silent-drop auto-complete path. That path attributed
usage but not delivered_text/tool_trace, leaving the daily reflection
blind on exactly the runs where the model already failed to close
cleanly (and the operator unable to verify what was sent from the run
record — this was noticed while checking a suspected cross-task
contamination that the trace disproved).
"""

import sys
import types

if "homunculus.tools.notify" not in sys.modules or not hasattr(
    sys.modules["homunculus.tools.notify"], "deliver"
):
    _stub = types.ModuleType("homunculus.tools.notify")
    _stub.deliver = lambda text: {"recorded": True, "delivered": [], "failed": []}
    _stub._send_to_telegram = lambda text: None
    sys.modules["homunculus.tools.notify"] = _stub

from homunculus.heartbeat import TaskGuard, _settle_silent_drop  # noqa: E402
from homunculus.tasks import TaskStore  # noqa: E402


def _store_with_task(tmp_path, task_id: str) -> TaskStore:
    store = TaskStore(tmp_path / "tasks")
    store.create(task_id.replace("-", " "), "", "2026-07-04T09:00:00", "daily", notify=True)
    return store


def test_auto_complete_attributes_delivery_evidence(tmp_path):
    store = _store_with_task(tmp_path, "daily-leetcode")
    task = store.all()[0]
    task["success_criteria"] = [{"type": "notify_called"}]

    guard = TaskGuard({task["id"]: task["success_criteria"]})
    guard.on_tool_call("leetcode_next_problem", {})
    guard.observe_tool_result("leetcode_next_problem", "NEXT PROBLEM: #14")
    guard.on_tool_call("notify", {"text": "**LeetCode Daily — Longest Common Prefix**"})
    # No complete_task — the model wrote its close-out as prose.

    _settle_silent_drop(store, task, guard, duration_s=10.0, usage={}, fire_escalation_notify=False)

    run = store.get(task["id"])["last_runs"][-1]
    assert run["status"] == "success"
    assert "Longest Common Prefix" in (run.get("delivered_text") or ""), (
        "auto-complete must record WHAT was delivered — the reflection "
        "self-critique reads it"
    )
    assert "notify" in (run.get("tool_trace") or "")


def test_partial_attributes_tool_trace(tmp_path):
    store = _store_with_task(tmp_path, "daily-leetcode")
    task = store.all()[0]
    task["success_criteria"] = [{"type": "notify_called"}]

    guard = TaskGuard({task["id"]: task["success_criteria"]})
    guard.on_tool_call("web_search", {"query": "x"})  # flailed, never notified

    _settle_silent_drop(store, task, guard, duration_s=10.0, usage={}, fire_escalation_notify=False)

    run = store.get(task["id"])["last_runs"][-1]
    assert run["status"] == "partial"
    assert "web_search" in (run.get("tool_trace") or ""), (
        "the reflection diagnoses a failed run from its tool trace"
    )
