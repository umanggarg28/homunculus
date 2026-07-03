"""Read-only Google Calendar/Gmail tools (roadmap NEW.1).

The contract under test: read-only by scope, sentinel (never exception)
when the account isn't connected, digested text output (never raw API
JSON), and email/calendar text treated as untrusted content.
"""

import json

from tests.conftest import load_real_tool_submodule

google_auth = load_real_tool_submodule("google_auth")
google_calendar = load_real_tool_submodule("google_calendar")
google_gmail = load_real_tool_submodule("google_gmail")


def _disconnect(tmp_path, monkeypatch):
    """Point the secrets dir at an empty tmp — the not-connected state."""
    monkeypatch.setenv("HOMUNCULUS_GOOGLE_SECRETS_DIR", str(tmp_path / "google"))
    monkeypatch.setattr(google_auth, "_access_cache", None)


# ------------------------------------------------------------- sentinels


def test_calendar_returns_sentinel_when_not_connected(tmp_path, monkeypatch):
    _disconnect(tmp_path, monkeypatch)
    out = google_calendar.calendar_events(1)
    assert out.startswith("CALENDAR_UNAVAILABLE")


def test_gmail_returns_sentinel_when_not_connected(tmp_path, monkeypatch):
    _disconnect(tmp_path, monkeypatch)
    assert google_gmail.gmail_unread(3).startswith("GMAIL_UNAVAILABLE")
    assert google_gmail.gmail_search("from:x", 3).startswith("GMAIL_UNAVAILABLE")


def test_gmail_search_requires_a_query(tmp_path, monkeypatch):
    _disconnect(tmp_path, monkeypatch)
    assert google_gmail.gmail_search("", 3).startswith("ERROR")


# ------------------------------------------------------------ formatting


def test_calendar_formatting_timed_allday_and_cancelled():
    items = [
        {"summary": "Standup", "status": "confirmed",
         "start": {"dateTime": "2026-07-04T10:00:00+05:30"},
         "end": {"dateTime": "2026-07-04T10:30:00+05:30"}},
        {"summary": "Holiday", "start": {"date": "2026-07-05"},
         "end": {"date": "2026-07-06"}},
        {"summary": "Ghost", "status": "cancelled",
         "start": {"dateTime": "2026-07-04T11:00:00+05:30"},
         "end": {"dateTime": "2026-07-04T12:00:00+05:30"}},
    ]
    lines = google_calendar._format_events(items)
    assert len(lines) == 2
    assert "Standup" in lines[0] and "10:00–10:30" in lines[0]
    assert "2026-07-05 · all day" in lines[1]
    assert not any("Ghost" in ln for ln in lines)


def test_gmail_message_formatting_and_age():
    msg = {
        "snippet": "Interview confirmed for Friday 3pm — please bring…",
        "internalDate": "1",  # epoch 1970 → days ago
        "payload": {"headers": [
            {"name": "From", "value": "HR <hr@thuli.studio>"},
            {"name": "Subject", "value": "Interview logistics"},
        ]},
    }
    lines = google_gmail._format_message(msg)
    assert lines[0].startswith("- HR <hr@thuli.studio> · Interview logistics")
    assert "d ago" in lines[0]
    assert lines[1].strip().startswith("Interview confirmed")


# --------------------------------------------------------- token refresh


def test_access_token_is_cached_across_calls(tmp_path, monkeypatch):
    sdir = tmp_path / "google"
    sdir.mkdir(parents=True)
    (sdir / "client_secret.json").write_text(json.dumps(
        {"installed": {"client_id": "cid", "client_secret": "sec"}}))
    (sdir / "token.json").write_text(json.dumps({"refresh_token": "rt"}))
    monkeypatch.setenv("HOMUNCULUS_GOOGLE_SECRETS_DIR", str(sdir))
    monkeypatch.setattr(google_auth, "_access_cache", None)

    posts = []

    class _Resp:
        def raise_for_status(self): ...
        def json(self): return {"access_token": "at-1", "expires_in": 3600}

    monkeypatch.setattr(google_auth.httpx, "post",
                        lambda *a, **k: posts.append(1) or _Resp())

    assert google_auth.get_access_token() == "at-1"
    assert google_auth.get_access_token() == "at-1"
    assert len(posts) == 1, "second call within expiry must hit the cache"


# -------------------------------------------------------------- security


def test_google_tools_are_untrusted_content_sources():
    from homunculus.security import _UNTRUSTED_CONTENT_TOOLS
    assert {"gmail_unread", "gmail_search", "calendar_events"} <= _UNTRUSTED_CONTENT_TOOLS


def test_scopes_are_read_only():
    for scope in google_auth.SCOPES:
        assert scope.endswith(".readonly"), scope
