"""Read-only Gmail tools (roadmap NEW.1).

The grant has only gmail.readonly — sending, drafting, and mark-as-read
are impossible at the OAuth layer, not merely discouraged in the prompt.
Output is digested sender/subject/age lines with the API's own short
snippet; full message bodies are deliberately not exposed in v1 (a
snippet is a bounded prompt-injection surface, a body is not).
"""

from __future__ import annotations
from homunculus.sentinels import GMAIL_UNAVAILABLE

from datetime import datetime, UTC

from .google_auth import api_get, unavailable_suffix

UNAVAILABLE = (
    f"{GMAIL_UNAVAILABLE}: Google account not connected (or the request "
    "failed). Omit email information rather than guessing."
)

_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
_MAX_RESULTS = 10


def gmail_unread(limit: int = 5) -> str:
    """The newest unread inbox messages as sender/subject/age lines."""
    return _digest("is:unread in:inbox", limit)


def gmail_search(query: str, limit: int = 5) -> str:
    """Gmail search (same syntax as the Gmail search box), digested."""
    query = (query or "").strip()
    if not query:
        return "ERROR: pass a Gmail search query, e.g. 'from:foo@bar.com newer_than:7d'."
    return _digest(query, limit)


def _digest(query: str, limit: int) -> str:
    limit = max(1, min(int(limit or 5), _MAX_RESULTS))
    listing = api_get(f"{_BASE}/messages", {"q": query, "maxResults": str(limit)})
    if listing is None:
        return UNAVAILABLE + unavailable_suffix()
    ids = [m["id"] for m in (listing.get("messages") or []) if m.get("id")]
    if not ids:
        return f"No messages match: {query}"

    # A scan that hit the cap covers less of the window than it was asked for.
    # Silently returning the newest N makes a partial scan indistinguishable
    # from a complete one: a "newer_than:3d" search of a busy mailbox really
    # covers the last several hours, and a skill reading it will report
    # "nothing new" for events it never saw. One missed interview invitation
    # is what this line exists to prevent.
    truncated = len(ids) >= limit

    lines: list[str] = []
    for mid in ids:
        msg = api_get(f"{_BASE}/messages/{mid}", {
            "format": "metadata",
            "metadataHeaders": ["From", "Subject", "Date"],
        })
        if msg is None:
            continue
        lines.extend(_format_message(msg))
    if truncated:
        lines.append(
            f"\n[TRUNCATED: showing the {len(ids)} most recent matches only — "
            f"older messages inside this query's window were NOT scanned. "
            f"Narrow the query (add keywords or a shorter window) before "
            f"concluding anything about what it does not contain.]"
        )
    return "\n".join(lines) if lines else UNAVAILABLE + unavailable_suffix()


def _format_message(msg: dict) -> list[str]:
    headers = {
        h.get("name", ""): h.get("value", "")
        for h in (msg.get("payload") or {}).get("headers") or []
    }
    sender = headers.get("From", "?")[:80]
    subject = (headers.get("Subject") or "(no subject)")[:120]
    age = _age(msg.get("internalDate"))
    snippet = (msg.get("snippet") or "").strip()[:200]
    lines = [f"- {sender} · {subject}{f' · {age}' if age else ''}"]
    if snippet:
        lines.append(f"  {snippet}")
    return lines


def _age(internal_date_ms) -> str:
    try:
        dt = datetime.fromtimestamp(int(internal_date_ms) / 1000, tz=UTC)
    except (TypeError, ValueError):
        return ""
    mins = max(0, int((datetime.now(UTC) - dt).total_seconds() // 60))
    if mins < 60:
        return f"{mins}m ago"
    if mins < 60 * 24:
        return f"{mins // 60}h ago"
    return f"{mins // (60 * 24)}d ago"
