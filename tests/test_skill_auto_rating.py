"""Phase 3a — the harness rates skills from the authoritative run verdict,
not the model's self-report.

The weak model is unreliable about calling rate_skill (and can fabricate it), so
during a heartbeat tick the harness records the outcome itself and suppresses the
model's own rate_skill call.
"""

from __future__ import annotations

from homunculus.heartbeat import TaskGuard, _rate_task_skill


def test_guard_suppresses_model_rate_skill_during_tick():
    """A rate_skill call during a tick is intercepted (not passed through) so it
    can't double-count or fabricate against the harness's authoritative rating."""
    guard = TaskGuard({"t1": [{"type": "notify_called"}]})
    result = guard.on_tool_call("rate_skill", {"name": "skill_x", "outcome": "success"})
    assert result is not None
    assert "automatically" in result.lower()


def test_other_tools_still_pass_through():
    guard = TaskGuard({"t1": []})
    assert guard.on_tool_call("read_file", {"path": "x"}) is None


class _FakeMemory:
    def __init__(self):
        self.calls = []

    def rate_skill(self, name, outcome, notes=""):
        self.calls.append((name, outcome))
        return "ok"


def test_rate_task_skill_strips_prefix_and_records():
    mem = _FakeMemory()
    _rate_task_skill(mem, {"id": "t1", "skill": "skill_hn_ai_summary"}, "success")
    assert mem.calls == [("hn_ai_summary", "success")]


def test_rate_task_skill_failure():
    mem = _FakeMemory()
    _rate_task_skill(mem, {"id": "t1", "skill": "skill_daily_brief"}, "failure")
    assert mem.calls == [("daily_brief", "failure")]


def test_rate_task_skill_noop_without_skill():
    mem = _FakeMemory()
    _rate_task_skill(mem, {"id": "t1", "skill": None}, "success")
    _rate_task_skill(mem, {"id": "t2"}, "success")
    assert mem.calls == []


def test_rate_task_skill_swallows_errors():
    class _Boom:
        def rate_skill(self, *a, **k):
            raise RuntimeError("disk full")
    # Must not raise — rating is best-effort, never breaks the tick.
    _rate_task_skill(_Boom(), {"id": "t1", "skill": "skill_x"}, "success")
