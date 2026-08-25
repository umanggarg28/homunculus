"""A committed event time must come from something a tool actually returned.

Live failure, 2026-08-24. `gmail_search` returned one real invitation —
"I have scheduled the call for 26th August at 3pm IST" — alongside a
truncated snippet of a different company's interview thread that contained no
time at all. The agent recorded the real 15:00 event, and then recorded a
SECOND reminder at 05:30 for "Interview with Juniper Square — Technical Lead":
a job title in no email, at midnight UTC, which is a timezone artifact rather
than anything anyone wrote.

Four reminders, two of them fabricated, all indistinguishable from real ones
once stored. This is the same rule `notify_links_grounded` applies to URLs: a
value the agent could not have read is one it invented.
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

# The real snippet, as gmail_search returned it.
REAL_EMAIL = (
    "- Muskan Nankani <mnankani@junipersquare.com> · Re: Next Steps with "
    "Juniper Square · 8h ago Hi Umang, Thank You for providing your "
    "availability. I have scheduled the call for 26th August at 3pm IST."
)


def _guard_with_email(blob: str = REAL_EMAIL) -> TaskGuard:
    g = TaskGuard({"t": []})
    g.on_tool_call("gmail_search", {"query": "interview"})
    g.observe_tool_result("gmail_search", blob)
    return g


def test_the_real_event_is_allowed():
    """3pm IST is written down, so 15:00+05:30 is grounded."""
    g = _guard_with_email()
    assert g.on_tool_call("record_commitment", {
        "what": "Next Steps call with Juniper Square",
        "event_at": "2026-08-26T15:00:00+05:30",
    }) is None


def test_the_fabricated_event_is_refused():
    """05:30 appears nowhere — it is midnight UTC, not a stated time."""
    g = _guard_with_email()
    blocked = g.on_tool_call("record_commitment", {
        "what": "Interview with Juniper Square — Technical Lead",
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
