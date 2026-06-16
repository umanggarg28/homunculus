"""news_feeds config — editable feed list with seeded defaults.

The user wanted news from multiple sources and the freedom to change them, so
the source list is data (a config file), not code. These pin the parse rules
and the topic-selection fallback.
"""

from __future__ import annotations

import news_feeds


def test_defaults_when_file_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMUNCULUS_NEWS_FEEDS_FILE", str(tmp_path / "nope.txt"))
    feeds = news_feeds.get_news_feeds()
    assert feeds == news_feeds.DEFAULT_FEEDS
    # All four seeded categories are present.
    labels = {l for l, _ in feeds}
    assert {"hackernews", "tech", "ai-research", "world"} <= labels


def test_parses_user_file_and_ignores_comments(tmp_path, monkeypatch):
    f = tmp_path / "feeds.txt"
    f.write_text(
        "# my feeds\n"
        "Tech | https://example.com/tech.rss\n"
        "\n"
        "world|https://example.com/world.xml\n"
        "garbage line without pipe\n"
        "bad | ftp://not-http.example\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOMUNCULUS_NEWS_FEEDS_FILE", str(f))
    feeds = news_feeds.get_news_feeds()
    assert feeds == [
        ("tech", "https://example.com/tech.rss"),
        ("world", "https://example.com/world.xml"),
    ]


def test_empty_file_falls_back_to_defaults(tmp_path, monkeypatch):
    f = tmp_path / "feeds.txt"
    f.write_text("# only comments\n\n", encoding="utf-8")
    monkeypatch.setenv("HOMUNCULUS_NEWS_FEEDS_FILE", str(f))
    assert news_feeds.get_news_feeds() == news_feeds.DEFAULT_FEEDS


def test_select_by_topic(tmp_path, monkeypatch):
    f = tmp_path / "feeds.txt"
    f.write_text(
        "tech | https://e.com/t\nworld | https://e.com/w\n", encoding="utf-8"
    )
    monkeypatch.setenv("HOMUNCULUS_NEWS_FEEDS_FILE", str(f))
    assert news_feeds.select_feeds("tech") == [("tech", "https://e.com/t")]
    # No match → all feeds (never an empty brief).
    assert len(news_feeds.select_feeds("sports")) == 2
    # Empty topic → all feeds.
    assert len(news_feeds.select_feeds("")) == 2
