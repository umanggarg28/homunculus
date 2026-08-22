"""Read-only Google Calendar tool (roadmap NEW.1).

Returns digested, ready-to-paste text — never raw API JSON. Sources are
data, fetch is code: the model asks for a window and gets bullet lines;
all parsing, timezone handling, and truncation happen here.
"""

from __future__ import annotations
from homunculus.sentinels import CALENDAR_UNAVAILABLE

from datetime import datetime, timedelta

from homunculus.user_tz import now_user_tz

from .google_auth import api_get

UNAVAILABLE = (
    f"{CALENDAR_UNAVAILABLE}: Google account not connected (or the request "
    "failed). Omit calendar information rather than guessing."
)

_EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
_MAX_EVENTS = 20


def calendar_events(days: int = 1) -> str:
    """Upcoming events from the user's primary calendar as bullet lines."""
    days = max(1, min(int(days or 1), 14))
    start = now_user_tz()
    end = start + timedelta(days=days)
    data = api_get(_EVENTS_URL, {
        "timeMin": start.isoformat(),
        "timeMax": end.isoformat(),
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": str(_MAX_EVENTS),
    })
    if data is None:
        return UNAVAILABLE
    lines = _format_events(data.get("items") or [])
    if not lines:
        return f"No calendar events in the next {days} day(s)."
    return "\n".join(lines)


def _format_events(items: list[dict]) -> list[str]:
    lines: list[str] = []
    for ev in items[:_MAX_EVENTS]:
        if ev.get("status") == "cancelled":
            continue
        title = (ev.get("summary") or "(no title)").strip()[:120]
        start, end = ev.get("start") or {}, ev.get("end") or {}
        if "date" in start:  # all-day event
            when = f"{start['date']} · all day"
        else:
            s = _parse(start.get("dateTime", ""))
            e = _parse(end.get("dateTime", ""))
            if s is None:
                continue
            when = s.strftime("%a %b %d %H:%M")
            if e is not None:
                when += f"–{e.strftime('%H:%M')}"
        loc = (ev.get("location") or "").strip()
        suffix = f" · {loc[:60]}" if loc else ""
        lines.append(f"- {when} · {title}{suffix}")
    return lines


def _parse(raw: str) -> datetime | None:
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None
