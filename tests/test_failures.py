"""Failure taxonomy: platform/transient vs genuine, and the reflection routing.

Regression: the disk outage (OSError errno 5) fed transient infrastructure
failures into the daily reflection's skill-review feed, which has no fix for
them — the weak model thrashed and emitted a fabricated skill-edit notice.
Reflection must learn only from genuine failures.
"""

from __future__ import annotations

import pytest

from homunculus import heartbeat
from homunculus.failures import is_transient_failure


@pytest.mark.parametrize("text", [
    "OSError: [Errno 5] Input/output error",
    "All providers exhausted",
    "provider_exhaustion · retry in ~10 min",
    "ConnectError: connection refused",
    "HTTP 429 Too Many Requests",
    "503 Service Unavailable",
    "No space left on device ([Errno 28])",
    "temporary failure in name resolution",
])
def test_transient_infrastructure_failures_detected(text):
    assert is_transient_failure(text) is True


@pytest.mark.parametrize("text", [
    "notify failed: success criteria not met",
    "skill produced no output this run",
    "HTTP 400 Bad Request",
    "validation error: missing required field",
    "fabricated link not grounded in any tool result",
    "",
    None,
])
def test_genuine_failures_are_not_transient(text):
    assert is_transient_failure(text) is False


# --- reflection routing: infra failures never reach skill review ----------

class _FakeTasks:
    def __init__(self, items):
        self._items = items

    def list(self, _status):
        return self._items


def _task(skill, status, result="", delivered_text=""):
    return {
        "id": f"task-{skill}", "skill": skill,
        "last_runs": [{"status": status, "result": result, "delivered_text": delivered_text}],
    }


def test_reflection_feed_omits_transient_infra_failure():
    tasks = _FakeTasks([_task("skill_leetcode", "failure",
                              result="OSError: [Errno 5] Input/output error")])
    out = heartbeat._format_recent_deliveries(tasks)
    assert "skill_leetcode" not in out  # routed away from skill review


def test_reflection_feed_keeps_genuine_failure():
    tasks = _FakeTasks([_task("skill_brief", "failure",
                              result="notify failed: success criteria not met")])
    out = heartbeat._format_recent_deliveries(tasks)
    assert "skill_brief" in out and "criteria not met" in out


def test_reflection_feed_keeps_successful_delivery():
    tasks = _FakeTasks([_task("skill_quiz", "success",
                              delivered_text="🧠 Quiz — what is a B-tree?")])
    out = heartbeat._format_recent_deliveries(tasks)
    assert "skill_quiz" in out


def test_reflection_feed_empty_when_all_failures_are_infra():
    tasks = _FakeTasks([
        _task("skill_a", "failure", result="All providers exhausted"),
        _task("skill_b", "failure", result="OSError: [Errno 5] Input/output error"),
    ])
    out = heartbeat._format_recent_deliveries(tasks)
    assert out == "(no recent skill-backed deliveries captured yet)"
