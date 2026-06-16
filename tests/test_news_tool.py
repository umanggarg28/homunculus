"""news_headlines — deterministic fetch+parse over configured feeds.

This is the (A) half of the anti-fabrication fix: a single tool returns REAL
links from the user's feeds, so the model never hand-builds a URL. One tool
covers every source (sources are config), not a per-site tool.
"""

from __future__ import annotations

import httpx

import news_feeds
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


def _rss(*items):
    body = "".join(
        f"<item><title>{t}</title><link>{u}</link></item>" for t, u in items
    )
    return f"<rss><channel><title>F</title>{body}</channel></rss>"


def _resp(url, text):
    # raise_for_status() needs a request bound to the response.
    return httpx.Response(200, text=text, request=httpx.Request("GET", url))


def test_returns_real_links_verbatim(tmp_path, monkeypatch):
    _feeds(tmp_path, monkeypatch, ("tech", "https://feed.example/a.rss"))
    doc = _rss(("First post", "https://news.example/1"),
               ("Second", "https://news.example/2"))
    monkeypatch.setattr(httpx, "get", lambda url, *a, **k: _resp(url, doc))
    out = news.news_headlines(limit=5)
    assert out == "- [First post](https://news.example/1)\n- [Second](https://news.example/2)"


def test_round_robin_across_feeds(tmp_path, monkeypatch):
    _feeds(tmp_path, monkeypatch,
           ("tech", "https://t.example/f"), ("world", "https://w.example/f"))

    def fake_get(url, *a, **k):
        if "t.example" in url:
            return _resp(url, _rss(("T1", "https://t/1"), ("T2", "https://t/2")))
        return _resp(url, _rss(("W1", "https://w/1"), ("W2", "https://w/2")))

    monkeypatch.setattr(httpx, "get", fake_get)
    out = news.news_headlines(limit=3)
    # Interleaved: one tech, one world, one tech — not all of one source first.
    assert out.splitlines() == [
        "- [T1](https://t/1)",
        "- [W1](https://w/1)",
        "- [T2](https://t/2)",
    ]


def test_unavailable_when_all_feeds_fail(tmp_path, monkeypatch):
    _feeds(tmp_path, monkeypatch, ("tech", "https://feed.example/a.rss"))

    def boom(*a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "get", boom)
    out = news.news_headlines()
    assert out.startswith("NEWS_UNAVAILABLE")


def test_skips_failed_feed_but_keeps_others(tmp_path, monkeypatch):
    _feeds(tmp_path, monkeypatch,
           ("dead", "https://dead.example/f"), ("live", "https://live.example/f"))

    def fake_get(url, *a, **k):
        if "dead" in url:
            raise httpx.ConnectError("down")
        return _resp(url, _rss(("Alive", "https://live/1")))

    monkeypatch.setattr(httpx, "get", fake_get)
    out = news.news_headlines(limit=5)
    assert out == "- [Alive](https://live/1)"


def test_drops_entries_without_http_link(tmp_path, monkeypatch):
    _feeds(tmp_path, monkeypatch, ("tech", "https://feed.example/a.rss"))
    doc = _rss(("No link", ""), ("Good", "https://news.example/ok"))
    monkeypatch.setattr(httpx, "get", lambda url, *a, **k: _resp(url, doc))
    out = news.news_headlines(limit=5)
    assert out == "- [Good](https://news.example/ok)"
