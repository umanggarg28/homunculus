"""A partial scan must not look like a complete one.

gmail_search caps results at _MAX_RESULTS. A "newer_than:3d" query against a
busy mailbox therefore covers the last several hours, not three days — and
returned the newest N silently. A skill reading that concluded "nothing new
to flag" on a morning when an interview invitation sat just outside the cap.
"""

from __future__ import annotations

from tests.conftest import load_real_tool_submodule

_gm = load_real_tool_submodule("google_gmail")


def _listing(n):
    return {"messages": [{"id": f"m{i}"} for i in range(n)]}


def _message(subject):
    return {
        "payload": {"headers": [
            {"name": "From", "value": "a@b.c"},
            {"name": "Subject", "value": subject},
        ]},
        "snippet": "",
    }


def _api(monkeypatch, count):
    calls = {"n": 0}

    def fake_get(path, params=None):
        if path.endswith("/messages"):
            return _listing(count)
        calls["n"] += 1
        return _message(f"subject {calls['n']}")

    monkeypatch.setattr(_gm, "api_get", fake_get)


def test_a_capped_scan_says_it_was_capped(monkeypatch):
    _api(monkeypatch, _gm._MAX_RESULTS)
    out = _gm.gmail_search("newer_than:3d", limit=_gm._MAX_RESULTS)
    assert "TRUNCATED" in out
    assert "NOT scanned" in out


def test_the_warning_tells_the_reader_not_to_infer_absence(monkeypatch):
    """The failure was concluding "nothing new" from a partial view."""
    _api(monkeypatch, _gm._MAX_RESULTS)
    out = _gm.gmail_search("newer_than:3d", limit=_gm._MAX_RESULTS)
    assert "before concluding anything about what it does not contain" in out.lower()


def test_a_complete_scan_is_not_labelled_truncated(monkeypatch):
    _api(monkeypatch, 3)
    out = _gm.gmail_search("newer_than:3d", limit=_gm._MAX_RESULTS)
    assert "TRUNCATED" not in out


def test_an_empty_result_is_unchanged(monkeypatch):
    monkeypatch.setattr(_gm, "api_get", lambda p, params=None: {"messages": []})
    assert "No messages match" in _gm.gmail_search("newer_than:3d")
