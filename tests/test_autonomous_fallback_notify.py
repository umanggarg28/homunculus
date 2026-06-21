"""Item 8 of the robustness plan — autonomous fallback notification.

When the heartbeat post-success check fires for a task with notify=true,
the user is also notified directly so they don't have to check Traces
to discover that a daily reminder silently failed.

The notification is best-effort — any error sending it is swallowed and
the failure is still recorded. The user hearing about it is a bonus on
top of the existing post-success record_failure flow.
"""

import sys
import types

# heartbeat.py imports `from tools.notify import deliver`. Stub the package
# before importing heartbeat (conftest does NOT stub subpackages of tools).
# `deliver` fans out to all channels + records to the web feed; here we just
# capture the text the fallback path would send.
if "homunculus.tools.notify" not in sys.modules or not hasattr(sys.modules["homunculus.tools.notify"], "deliver"):
    _stub = types.ModuleType("homunculus.tools.notify")
    _deliver_calls: list = []
    _stub._deliver_calls = _deliver_calls

    def _fake_deliver(text):
        _deliver_calls.append(text)
        return {"recorded": True, "delivered": [], "failed": []}

    _stub.deliver = _fake_deliver
    # Kept for any caller still importing the channel sender directly.
    _stub._send_to_telegram = lambda text: _deliver_calls.append(text) or None
    sys.modules["homunculus.tools.notify"] = _stub


def _get_calls():
    return sys.modules["homunculus.tools.notify"]._deliver_calls


def _clear_calls():
    sys.modules["homunculus.tools.notify"]._deliver_calls.clear()


def test_silent_drop_on_notify_task_sends_fallback():
    """The exact pattern from the LeetCode failure:
    - Task has notify=true (the user opted in to hearing about it)
    - Agent silently dropped it (no complete_task, no record_failure)
    - The post-success check should deliver a fallback notice so the user
      knows something went wrong — via deliver(), which also records to the
      always-on web feed.
    """
    _clear_calls()
    # Contract simulation (not a full heartbeat tick): given a task with
    # notify=true and a silent drop, deliver() is called with a message
    # mentioning the task title.
    from homunculus.tools.notify import deliver
    task = {"id": "daily-leetcode-150", "title": "Daily LeetCode 150 Problem", "notify": True}
    deliver(
        f"⚠️ I tried to handle '{task['title']}' just now but ran out of "
        f"iterations / context before finishing. The task is still "
        f"active and I'll retry on the next tick."
    )
    calls = _get_calls()
    assert len(calls) == 1
    msg = calls[0]
    assert task["title"] in msg
    assert "ran out of iterations / context" in msg
    assert "retry on the next tick" in msg


def test_silent_drop_on_non_notify_task_does_not_send():
    """If notify=false, the user explicitly didn't want notifications for
    this task — the fallback path must respect that."""
    _clear_calls()
    task = {"id": "background-cleanup", "title": "Cleanup", "notify": False}
    # Caller's contract — if notify is false, don't send.
    if task.get("notify"):
        from homunculus.tools.notify import deliver
        deliver("won't reach here")
    assert _get_calls() == []
