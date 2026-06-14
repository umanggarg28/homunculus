"""Delivery ledger + notify_unique — deterministic "don't repeat yourself"
for recurring delivery tasks.

The LeetCode task tracked delivered problems in an LLM-maintained
markdown file. Within three weeks the format degraded (a JSON array
with stray appended bullets) and the agent re-delivered "Best Time to
Buy and Sell Stock II" the day after sending it — with an algomap.io
link instead of LeetCode, because it had improvised the whole flow via
web_search.

The fix moves WHAT-was-sent bookkeeping into the harness:

  - TaskStore.record_delivery() keeps a per-task `delivered` ledger.
  - The notify_unique criterion extracts a delivery key (regex) from
    the notify text and blocks the send if the key is in the ledger.
  - The heartbeat records keys from SENT notify texts — ground truth
    for what reached the user — on success and crash paths alike.

OSS lineage: Letta separates agent state from agent memory; Mem0 keys
facts so re-insertion is idempotent. Same idea: dedupe is a property
of the store, not of the model's diligence.
"""

import importlib.util
import sys
import types
from pathlib import Path

if "tools.notify" not in sys.modules:
    _stub = types.ModuleType("tools.notify")
    _telegram_calls: list[str] = []
    _stub._send_to_telegram = lambda text: _telegram_calls.append(text) or None
    _stub._telegram_calls = _telegram_calls
    sys.modules["tools.notify"] = _stub

from heartbeat import TaskGuard, _format_due_tasks, _record_delivery_keys  # noqa: E402


def _real_tasks_module():
    """Load tasks.py directly, bypassing any sys.modules stub."""
    spec = importlib.util.spec_from_file_location(
        "tasks_real_ledger_test", Path(__file__).parent.parent / "tasks.py"
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


UNIQUE = {
    "type": "notify_unique",
    "pattern": r"leetcode\.com/problems/([a-z0-9-]+)",
}


# ---- TaskStore.record_delivery --------------------------------------------

def test_record_delivery_appends_and_dedupes(tmp_path):
    store = _real_tasks_module().TaskStore(tmp_path)
    task = store.create("Daily LeetCode", recurrence="daily", due_at="2026-06-11T09:00:00")
    store.record_delivery(task["id"], "Two-Sum")          # normalized to lowercase
    store.record_delivery(task["id"], "two-sum")          # duplicate → ignored
    store.record_delivery(task["id"], "rotate-array")
    delivered = store.get(task["id"])["delivered"]
    assert [d["key"] for d in delivered] == ["two-sum", "rotate-array"]
    assert all(d.get("ts") for d in delivered)


def test_record_delivery_ignores_empty_key_and_caps(tmp_path):
    mod = _real_tasks_module()
    store = mod.TaskStore(tmp_path)
    task = store.create("Daily feed", recurrence="daily", due_at="2026-06-11T09:00:00")
    store.record_delivery(task["id"], "   ")
    assert store.get(task["id"]).get("delivered") in (None, [])
    cap = mod.TaskStore.DELIVERED_KEYS_CAP
    for i in range(cap + 5):
        store.record_delivery(task["id"], f"item-{i}")
    delivered = store.get(task["id"])["delivered"]
    assert len(delivered) == cap
    assert delivered[0]["key"] == "item-5"               # oldest dropped first


def test_format_recent_deliveries_digest():
    """The reflection digest hands the model each skill-backed task's most
    recent run — success deliveries with their text, failures with the
    result — and skips tasks with no skill / no runs / no captured text."""
    from heartbeat import _format_recent_deliveries

    class _FakeStore:
        def __init__(self, tasks):
            self._tasks = tasks

        def list(self, status):  # noqa: ARG002 - mirrors TaskStore.list
            return self._tasks

    out = _format_recent_deliveries(_FakeStore([
        {"id": "hn", "skill": "skill_hn",
         "last_runs": [{"status": "success",
                        "delivered_text": "HN Summary\n- item https://x"}]},
        {"id": "lc", "skill": "skill_lc",
         "last_runs": [{"status": "failure", "result": "BLOCKED: no link"}]},
        {"id": "noskill", "last_runs": [{"status": "success", "delivered_text": "x"}]},
        {"id": "noruns", "skill": "skill_z", "last_runs": []},
        {"id": "uncaptured", "skill": "skill_o", "last_runs": [{"status": "success"}]},
    ]))
    assert "task: hn" in out and "HN Summary" in out
    assert "task: lc" in out and "BLOCKED: no link" in out
    assert "noskill" not in out      # no skill → skipped
    assert "noruns" not in out       # no runs → skipped
    assert "uncaptured" not in out   # success but no delivered_text → skipped


def test_format_recent_deliveries_empty():
    from heartbeat import _format_recent_deliveries

    class _Empty:
        def list(self, status):  # noqa: ARG002
            return []

    assert "no recent" in _format_recent_deliveries(_Empty()).lower()


def test_combined_notify_text_captures_sent_text():
    """The accessor returns what notify() actually sent — the reflection
    retrofits this as delivered_text for its quality self-critique."""
    guard = TaskGuard({"t1": [{"type": "notify_called"}]})
    assert guard.combined_notify_text() == ""
    guard.on_tool_call("notify", {"text": "Hacker News AI Summary - item one"})
    guard.on_tool_call("notify", {"text": "second line"})
    combined = guard.combined_notify_text()
    assert "Hacker News AI Summary - item one" in combined
    assert "second line" in combined


# ---- notify_unique criterion ----------------------------------------------

def test_notify_unique_blocks_already_delivered_key():
    guard = TaskGuard(
        {"t1": [UNIQUE]},
        delivered_by_task={"t1": {"two-sum", "rotate-array"}},
    )
    blocked = guard.on_tool_call(
        "notify",
        {"text": "Today: Two Sum — https://leetcode.com/problems/two-sum/"},
    )
    assert blocked is not None
    assert "already delivered" in blocked


def test_notify_unique_allows_novel_key_and_complete():
    guard = TaskGuard(
        {"t1": [UNIQUE]},
        delivered_by_task={"t1": {"two-sum"}},
    )
    sent = guard.on_tool_call(
        "notify",
        {"text": "Today: Jump Game — https://leetcode.com/problems/jump-game/"},
    )
    assert sent is None
    assert guard.on_tool_call("complete_task", {"task_id": "t1"}) is None
    assert guard.delivery_key("t1") == "jump-game"


def test_notify_unique_requires_extractable_key():
    """A notify with no canonical link can't prove novelty — block it.
    This also kills the improvised-source failure (algomap.io link)."""
    guard = TaskGuard({"t1": [UNIQUE]}, delivered_by_task={"t1": set()})
    blocked = guard.on_tool_call(
        "notify",
        {"text": "Today: Jump Game — https://algomap.io/problems/jump-game"},
    )
    assert blocked is not None
    assert "no delivery key" in blocked


def test_delivery_key_none_without_unique_criterion_or_send():
    guard = TaskGuard({"t1": [{"type": "notify_called"}]})
    guard.on_tool_call("notify", {"text": "hello"})
    assert guard.delivery_key("t1") is None
    guard2 = TaskGuard({"t1": [UNIQUE]}, delivered_by_task={"t1": set()})
    assert guard2.delivery_key("t1") is None              # nothing sent yet


# ---- heartbeat wiring -------------------------------------------------------

def test_record_delivery_keys_persists_sent_key(tmp_path):
    store = _real_tasks_module().TaskStore(tmp_path)
    task = store.create("Daily LeetCode", recurrence="daily", due_at="2026-06-11T09:00:00")
    guard = TaskGuard({task["id"]: [UNIQUE]}, delivered_by_task={task["id"]: set()})
    guard.on_tool_call(
        "notify",
        {"text": "Jump Game — https://leetcode.com/problems/jump-game/ ..."},
    )
    _record_delivery_keys(store, guard, [task])
    delivered = store.get(task["id"])["delivered"]
    assert [d["key"] for d in delivered] == ["jump-game"]


def test_format_due_tasks_lists_already_delivered():
    block = _format_due_tasks([{
        "id": "daily-leetcode",
        "title": "Daily LeetCode",
        "due_at": "2026-06-11T09:00:00",
        "recurrence": "daily",
        "notify": True,
        "description": "send one problem",
        "delivered": [
            {"key": "two-sum", "ts": "2026-06-09T09:00:00"},
            {"key": "rotate-array", "ts": "2026-06-10T09:00:00"},
        ],
    }])
    assert "already_delivered" in block
    assert "two-sum, rotate-array" in block


def test_format_due_tasks_forced_overrides_future_due_at():
    """Manual run-now: a future due_at must not let the model skip the task.

    Regression for the 2026-06-14 HN run where due_at was 3 days out, the
    model read it as "not due", and bailed without running the skill."""
    task = {
        "id": "weekly-hn",
        "title": "Weekly HN",
        "due_at": "2026-06-17T09:00:00",  # future
        "recurrence": "weekly",
        "notify": True,
        "description": "summarize HN",
    }
    forced = _format_due_tasks([task], forced=True)
    assert "manually triggered" in forced.lower()
    assert "regardless of the scheduled due_at" in forced
    # Default (scheduled-tick) rendering carries no such override.
    assert "manually triggered" not in _format_due_tasks([task]).lower()
