"""A committed event time must come from something a tool actually returned.

A search returns several threads, only some of which state a time. Given a
thread that states none, the model supplies one anyway — typically midnight
in the source's timezone, which surfaces as an odd-looking local hour. Once
stored, a fabricated reminder is indistinguishable from a real one.

This is the rule `notify_links_grounded` applies to URLs, applied to times:
a value the agent could not have read is one it invented.
"""

from __future__ import annotations

import sys
import types

if "homunculus.tools.notify" not in sys.modules:
    _stub = types.ModuleType("homunculus.tools.notify")
    _stub._send_to_telegram = lambda *_a, **_kw: None
    _stub.deliver = lambda *_a, **_kw: None
    sys.modules["homunculus.tools.notify"] = _stub

from homunculus.task_guard import TaskGuard, _time_forms  # noqa: E402

# A search result in the shape gmail_search returns: sender, subject, snippet.
SOURCE_EMAIL = (
    "- Dana Reyes <dreyes@acme.example> · Re: Next Steps with Acme · 8h ago "
    "Thank you for providing your availability. I have scheduled the call "
    "for 26th August at 3pm IST."
)


def _guard_with_email(blob: str = SOURCE_EMAIL) -> TaskGuard:
    g = TaskGuard({"t": []})
    g.on_tool_call("gmail_search", {"query": "interview"})
    g.observe_tool_result("gmail_search", blob)
    return g


def test_the_real_event_is_allowed():
    """3pm IST is written down, so 15:00+05:30 is grounded."""
    g = _guard_with_email()
    assert g.on_tool_call("record_commitment", {
        "what": "Next Steps call with Acme",
        "event_at": "2026-08-26T15:00:00+05:30",
    }) is None


def test_the_fabricated_event_is_refused():
    """05:30 appears nowhere — it is midnight UTC, not a stated time."""
    g = _guard_with_email()
    blocked = g.on_tool_call("record_commitment", {
        "what": "Interview with Acme — Technical Lead",
        "event_at": "2026-08-26T05:30:00+05:30",
    })
    assert blocked is not None and blocked.startswith("BLOCKED")
    assert "05:30" in blocked
    # The refusal steers rather than merely denying.
    assert "quote the text" in blocked


def test_a_24_hour_source_is_also_grounded():
    """A calendar API writes 15:00, not 3pm; both must count."""
    g = _guard_with_email("- Tue Aug 26 15:00–15:45 · Next Steps call")
    assert g.on_tool_call("record_commitment", {
        "what": "Next Steps call", "event_at": "2026-08-26T15:00:00+05:30",
    }) is None


def test_a_commitment_with_no_tool_output_is_untouched():
    """From chat or a task description there is nothing to ground against, and
    refusing those would break the ordinary case."""
    g = TaskGuard({"t": []})
    assert g.on_tool_call("record_commitment", {
        "what": "Call the dentist", "event_at": "2026-08-26T05:30:00+05:30",
    }) is None


def test_a_malformed_event_at_is_not_blocked_here():
    """Shape is `record_commitment`'s own business; this guard only judges
    whether a well-formed time is grounded."""
    g = _guard_with_email()
    assert g.on_tool_call("record_commitment", {
        "what": "x", "event_at": "next tuesday",
    }) is None


def test_time_forms_cover_both_conventions():
    forms = _time_forms("2026-08-26T15:00:00+05:30")
    assert "15:00" in forms and "3pm" in forms and "3:00pm" in forms
    half = _time_forms("2026-08-26T09:30:00+05:30")
    assert "09:30" in half and "9:30am" in half
    assert _time_forms("not a date") == []
