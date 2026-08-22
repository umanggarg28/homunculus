"""A skill's scorecard must only count that skill's own mistakes.

The evals table read "degrading" for six of seven skills at once, with an
identical violation shape across unrelated tasks: a long run of zeros then
2 · 2 · 2. Nothing about those skills had changed. The whole log held only
seven violation events, six of them from the nightly reflection tick — and
`events.emit` attributes an event to the active task context, which
reflection never set. `_events_between` keeps unstamped events in EVERY
task's window (dropping them would rewrite historical metrics), so one
failing reflection was charged to every skill in the system.

That is a worse failure than a wrong number: it points the self-improvement
loop at skills that are working.
"""

from __future__ import annotations

import sys
import types

if "homunculus.tools.notify" not in sys.modules:
    _stub = types.ModuleType("homunculus.tools.notify")
    _stub._send_to_telegram = lambda *_a, **_kw: None
    _stub.deliver = lambda *_a, **_kw: None
    sys.modules["homunculus.tools.notify"] = _stub

from homunculus.evals import _events_between  # noqa: E402


def _ev(ts: str, event: str, **kw):
    return {"ts": ts, "event": event, **kw}


def test_another_owners_violation_is_excluded():
    events = [
        _ev("2026-08-20T19:10", "required_tool_violation", task="reflection"),
        _ev("2026-08-20T19:11", "required_tool_violation", task="other-task"),
    ]
    window = _events_between(events, "2026-08-20T09:00", "2026-08-20T23:00", "morning-brief")
    assert window == [], "a violation owned by someone else must not be charged here"


def test_the_tasks_own_violation_is_kept():
    events = [_ev("2026-08-20T10:05", "required_tool_violation", task="morning-brief")]
    window = _events_between(events, "2026-08-20T09:00", "2026-08-20T11:00", "morning-brief")
    assert len(window) == 1


def test_reflection_stamps_an_owner_on_its_events():
    """The fix at its source: without an owner the exclusion above cannot fire,
    because 'unstamped' is indistinguishable from 'belongs to this task'."""
    from homunculus.heartbeat import REFLECTION_OWNER

    assert REFLECTION_OWNER
    events = [_ev("2026-08-20T19:10", "required_tool_violation", task=REFLECTION_OWNER)]
    assert _events_between(events, None, "2026-08-20T23:00", "morning-brief") == []
    # And it is still visible to anyone asking about reflection itself.
    assert len(_events_between(events, None, "2026-08-20T23:00", REFLECTION_OWNER)) == 1


def test_unstamped_events_are_still_kept():
    """Deliberate, and the reason the bug survived: historical events predate
    stamping, and dropping them would silently rewrite every past metric."""
    events = [_ev("2026-08-20T10:05", "required_tool_violation")]
    window = _events_between(events, "2026-08-20T09:00", "2026-08-20T11:00", "morning-brief")
    assert len(window) == 1
