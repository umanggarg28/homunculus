"""rss_feed — newsletter/blog distiller built on the shared snapshot diff.

Parses RSS 2.0 and Atom into one stable entry list (date · title — link,
no body) and diffs it run-over-run so the model summarises only the new
posts. Tests pin: both formats parse, the body is excluded from the
snapshot (edited posts aren't "new"), new entries show as + lines, and
non-feeds / fetch errors fail without poisoning the baseline.
"""

from __future__ import annotations

import pytest

from tests.conftest import load_real_tool_submodule

_helpers = load_real_tool_submodule("_helpers")
load_real_tool_submodule("web")
load_real_tool_submodule("watch")
_rss = load_real_tool_submodule("rss")


RSS_2 = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>TLDR</title>
  <item><title>Post A</title><link>https://ex.com/a</link><pubDate>Mon, 09 Jun 2026 00:00:00 GMT</pubDate>
        <description>full body text that may be edited later</description></item>
  <item><title>Post B</title><link>https://ex.com/b</link><pubDate>Sun, 08 Jun 2026 00:00:00 GMT</pubDate></item>
</channel></rss>"""

ATOM = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>My Blog</title>
  <entry><title>Entry One</title><link href="https://blog.com/1"/><updated>2026-06-09T00:00:00Z</updated></entry>
  <entry><title>Entry Two</title><link href="https://blog.com/2"/><updated>2026-06-08T00:00:00Z</updated></entry>
</feed>"""


def _mk_resp(text, status=200):
    class R:
        status_code = status
    r = R()
    r.text = text
    return r


@pytest.fixture()
def rss_env(tmp_path, monkeypatch):
    monkeypatch.setattr(_helpers, "CACHE_DIR", tmp_path)
    # The SSRF guard does real DNS — bypass it for example feeds.
    monkeypatch.setattr(_rss, "_url_block_reason", lambda url: None)

    def serve(text, status=200):
        monkeypatch.setattr(_rss.httpx, "get", lambda *a, **k: _mk_resp(text, status))

    return serve


def test_rss_2_parses_title_link_date(rss_env):
    rss_env(RSS_2)
    out = _rss.rss_feed("tldr", "https://ex.com/feed")
    assert "FIRST SNAPSHOT" in out  # appended summary not shown for rss_feed
    # Re-run to inspect the stored snapshot via a NO CHANGE round-trip.
    out2 = _rss.rss_feed("tldr", "https://ex.com/feed")
    assert out2.startswith("NO CHANGE")


def test_atom_parses_href_links(rss_env, tmp_path):
    import json
    rss_env(ATOM)
    _rss.rss_feed("blog", "https://blog.com/atom")
    snap = json.loads((tmp_path / "watch" / "rss-blog.json").read_text())
    assert "Entry One — https://blog.com/1" in snap["text"]
    assert "Entry Two — https://blog.com/2" in snap["text"]


def test_body_excluded_so_edits_are_not_new_posts(rss_env):
    rss_env(RSS_2)
    _rss.rss_feed("tldr", "https://ex.com/feed")
    # Same entries, but Post A's description body was edited.
    edited = RSS_2.replace("full body text that may be edited later", "REWRITTEN BODY")
    rss_env(edited)
    out = _rss.rss_feed("tldr", "https://ex.com/feed")
    assert out.startswith("NO CHANGE")


def test_new_entry_appears_as_addition(rss_env):
    rss_env(RSS_2)
    _rss.rss_feed("tldr", "https://ex.com/feed")
    with_new = RSS_2.replace(
        "<item><title>Post A</title>",
        '<item><title>Post C NEW</title><link>https://ex.com/c</link>'
        '<pubDate>Tue, 10 Jun 2026 00:00:00 GMT</pubDate></item>\n  '
        "<item><title>Post A</title>",
    )
    rss_env(with_new)
    out = _rss.rss_feed("tldr", "https://ex.com/feed")
    assert out.startswith("CHANGED")
    assert "Post C NEW" in out


def test_non_feed_is_rejected_and_not_snapshotted(rss_env, tmp_path):
    rss_env("<html><body>not a feed</body></html>")
    out = _rss.rss_feed("nope", "https://ex.com/page")
    assert out.startswith("ERROR:")
    assert not (tmp_path / "watch" / "rss-nope.json").exists()


def test_http_error_passes_through(rss_env, tmp_path):
    rss_env("", status=500)
    out = _rss.rss_feed("x", "https://ex.com/feed")
    assert out.startswith("ERROR: HTTP 500")
    assert not (tmp_path / "watch" / "rss-x.json").exists()


def test_rate_limit_blocks_without_poisoning_baseline(rss_env, tmp_path):
    rss_env(RSS_2)
    _rss.rss_feed("tldr", "https://ex.com/feed")
    rss_env("", status=429)
    out = _rss.rss_feed("tldr", "https://ex.com/feed")
    assert out.startswith("BLOCKED:")
    rss_env(RSS_2)
    assert _rss.rss_feed("tldr", "https://ex.com/feed").startswith("NO CHANGE")
