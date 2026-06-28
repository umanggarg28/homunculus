"""news_headlines — deterministic fetch + rank + parse over configured feeds.

This is the (A) half of the anti-fabrication fix: a single tool returns REAL
links from the user's feeds, so the model never hand-builds a URL. One tool
covers every source (sources are config), not a per-site tool.

Ranking (not round-robin): engagement where it exists (HN "Points: N"),
recency as the universal axis, a freshness floor for the stale tail, and a
per-source cap so the mix stays broad.
"""

from __future__ import annotations

from datetime import datetime, timedelta, UTC
from email.utils import format_datetime

import httpx

from tests.conftest import load_real_tool_submodule

load_real_tool_submodule("_helpers")
load_real_tool_submodule("web")
load_real_tool_submodule("watch")
load_real_tool_submodule("rss")
news = load_real_tool_submodule("news")


def _feeds(tmp_path, monkeypatch, *pairs):
    f = tmp_path / "feeds.txt"
    f.write_text("\n".join(f"{l} | {u}" for l, u in pairs), encoding="utf-8")
    monkeypatch.setenv("HOMUNCULUS_NEWS_FEEDS_FILE", str(f))
    # httpx.get is monkeypatched per test (no real network); disable the SSRF
    # DNS-resolve guard so example hostnames aren't dropped before the fetch.
    monkeypatch.setenv("HOMUNCULUS_ALLOW_PRIVATE_URLS", "1")


def _rfc822(hours_ago: float) -> str:
    return format_datetime(datetime.now(UTC) - timedelta(hours=hours_ago))


def _item(title, url, *, points=None, hours_ago=1.0):
    """One RSS <item>. HN feeds put 'Points: N' in the description body."""
    desc = f"Points: {points}" if points is not None else ""
    return (
        f"<item><title>{title}</title><link>{url}</link>"
        f"<description>{desc}</description>"
        f"<pubDate>{_rfc822(hours_ago)}</pubDate></item>"
    )


def _rss(*items):
    return f"<rss><channel><title>F</title>{''.join(items)}</channel></rss>"


def _resp(url, text):
    return httpx.Response(200, text=text, request=httpx.Request("GET", url))


def test_returns_real_links_verbatim(tmp_path, monkeypatch):
    _feeds(tmp_path, monkeypatch, ("tech", "https://feed.example/a.rss"))
    doc = _rss(_item("First post", "https://news.example/1", hours_ago=1),
               _item("Second", "https://news.example/2", hours_ago=2))
    monkeypatch.setattr(httpx, "get", lambda url, *a, **k: _resp(url, doc))
    out = news.news_headlines(limit=5)
    # Both real links present, freshest first.
    assert out == "- [First post](https://news.example/1)\n- [Second](https://news.example/2)"


def test_high_engagement_outranks_chronological(tmp_path, monkeypatch):
    _feeds(tmp_path, monkeypatch,
           ("hackernews", "https://hn.example/f"), ("world", "https://w.example/f"))

    def fake_get(url, *a, **k):
        if "hn.example" in url:
            return _resp(url, _rss(_item("HN big", "https://hn/1", points=400, hours_ago=5)))
        return _resp(url, _rss(_item("World fresh", "https://w/1", hours_ago=1)))

    monkeypatch.setattr(httpx, "get", fake_get)
    out = news.news_headlines(limit=2)
    # The 400-point HN story ranks above a fresher but unranked world item.
    assert out.splitlines()[0] == "- [HN big](https://hn/1)"


def test_per_source_cap_keeps_mix(tmp_path, monkeypatch):
    _feeds(tmp_path, monkeypatch,
           ("hackernews", "https://hn.example/f"), ("world", "https://w.example/f"))

    def fake_get(url, *a, **k):
        if "hn.example" in url:
            return _resp(url, _rss(
                _item("HN1", "https://hn/1", points=500, hours_ago=1),
                _item("HN2", "https://hn/2", points=450, hours_ago=1),
                _item("HN3", "https://hn/3", points=400, hours_ago=1),
            ))
        return _resp(url, _rss(_item("World", "https://w/1", hours_ago=1)))

    monkeypatch.setattr(httpx, "get", fake_get)
    out = news.news_headlines(limit=3).splitlines()
    # HN would sweep all 3 by score, but the per-source cap (2) reserves room
    # for the world item — broad mix preserved.
    assert sum(1 for l in out if "https://hn/" in l) == 2
    assert any("https://w/1" in l for l in out)


def test_stale_chronological_items_dropped(tmp_path, monkeypatch):
    _feeds(tmp_path, monkeypatch, ("world", "https://w.example/f"))
    doc = _rss(
        _item("Fresh", "https://w/fresh", hours_ago=2),
        _item("Ancient", "https://w/old", hours_ago=200),  # > 48h floor
    )
    monkeypatch.setattr(httpx, "get", lambda url, *a, **k: _resp(url, doc))
    out = news.news_headlines(limit=5)
    assert "https://w/fresh" in out
    assert "https://w/old" not in out


def test_old_hn_story_survives_freshness_floor(tmp_path, monkeypatch):
    _feeds(tmp_path, monkeypatch, ("hackernews", "https://hn.example/f"))
    # A high-point HN story older than 48h is still relevant (points gate it).
    doc = _rss(_item("HN classic", "https://hn/1", points=350, hours_ago=200))
    monkeypatch.setattr(httpx, "get", lambda url, *a, **k: _resp(url, doc))
    out = news.news_headlines(limit=5)
    assert "https://hn/1" in out


def test_unavailable_when_all_feeds_fail(tmp_path, monkeypatch):
    _feeds(tmp_path, monkeypatch, ("tech", "https://feed.example/a.rss"))

    def boom(*a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "get", boom)
    assert news.news_headlines().startswith("NEWS_UNAVAILABLE")


def test_skips_failed_feed_but_keeps_others(tmp_path, monkeypatch):
    _feeds(tmp_path, monkeypatch,
           ("dead", "https://dead.example/f"), ("live", "https://live.example/f"))

    def fake_get(url, *a, **k):
        if "dead" in url:
            raise httpx.ConnectError("down")
        return _resp(url, _rss(_item("Alive", "https://live/1", hours_ago=1)))

    monkeypatch.setattr(httpx, "get", fake_get)
    assert news.news_headlines(limit=5) == "- [Alive](https://live/1)"


def test_drops_entries_without_http_link(tmp_path, monkeypatch):
    _feeds(tmp_path, monkeypatch, ("tech", "https://feed.example/a.rss"))
    doc = _rss(_item("No link", "", hours_ago=1),
               _item("Good", "https://news.example/ok", hours_ago=1))
    monkeypatch.setattr(httpx, "get", lambda url, *a, **k: _resp(url, doc))
    assert news.news_headlines(limit=5) == "- [Good](https://news.example/ok)"


def _status_resp(url, status, text=""):
    return httpx.Response(status, text=text, request=httpx.Request("GET", url))


def test_retries_on_transient_5xx_then_succeeds(tmp_path, monkeypatch):
    """A single feed that 5xxes once then recovers must still return its items —
    a topic-scoped call can resolve to one feed, so one blip can't empty it."""
    _feeds(tmp_path, monkeypatch, ("hn-ai", "https://hnrss.test/ai"))
    monkeypatch.setattr("time.sleep", lambda *_: None)  # no real backoff in tests
    doc = _rss(_item("Real story", "https://news.ycombinator.com/item?id=123456",
                     points=80, hours_ago=1))
    calls = {"n": 0}

    def flaky_get(url, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return _status_resp(url, 502)  # transient
        return _resp(url, doc)

    monkeypatch.setattr(httpx, "get", flaky_get)
    out = news.news_headlines(topic="hn-ai", limit=5)
    assert out == "- [Real story](https://news.ycombinator.com/item?id=123456)"
    assert calls["n"] == 2, "should have retried once after the 502"


def test_gives_up_after_attempts_on_persistent_5xx(tmp_path, monkeypatch):
    """Persistent 5xx exhausts the retries and degrades to NEWS_UNAVAILABLE —
    a clean signal to omit the section, never a thrash."""
    _feeds(tmp_path, monkeypatch, ("hn-ai", "https://hnrss.test/ai"))
    monkeypatch.setattr("time.sleep", lambda *_: None)
    calls = {"n": 0}

    def always_500(url, *a, **k):
        calls["n"] += 1
        return _status_resp(url, 503)

    monkeypatch.setattr(httpx, "get", always_500)
    out = news.news_headlines(topic="hn-ai", limit=5)
    assert out.startswith("NEWS_UNAVAILABLE")
    assert calls["n"] == news._FETCH_ATTEMPTS, "should retry up to the attempt cap"


def test_4xx_is_not_retried(tmp_path, monkeypatch):
    """A 4xx is the caller's fault and won't fix itself — fetch once, skip."""
    _feeds(tmp_path, monkeypatch, ("hn-ai", "https://hnrss.test/ai"))
    monkeypatch.setattr("time.sleep", lambda *_: None)
    calls = {"n": 0}

    def bad_request(url, *a, **k):
        calls["n"] += 1
        return _status_resp(url, 400)

    monkeypatch.setattr(httpx, "get", bad_request)
    out = news.news_headlines(topic="hn-ai", limit=5)
    assert out.startswith("NEWS_UNAVAILABLE")
    assert calls["n"] == 1, "4xx must not be retried"
