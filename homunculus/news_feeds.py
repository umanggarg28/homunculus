"""News-feed config — the editable source list behind the `news_headlines` tool.

A single configurable feed list, not one bespoke tool per site. Every source
worth following (Hacker News, tech press, arXiv, world news) exposes RSS/Atom,
so adding a source is a one-line config edit — never new code. This is the
"scripts not inline parsing" pattern (Hermes / Anthropic skills): the mechanical
fetch+parse lives in code, and the set of sources is data.

Storage: workspace/news_feeds.txt, one feed per line as ``<label> | <url>``.
Blank lines and ``#`` comments are ignored. When the file is absent we fall
back to DEFAULT_FEEDS so the tool works out of the box; the user edits the file
to add/remove sources without touching code.

The ``label`` doubles as a coarse topic tag — ``news_headlines(topic="tech")``
prefers feeds whose label contains "tech". All feeds are free RSS with no API
key, so this respects the budget cap.
"""

from __future__ import annotations

import os
from pathlib import Path

# Seeded with all four categories the user asked for. Each is free RSS/Atom,
# no key. The user edits workspace/news_feeds.txt to change this set.
DEFAULT_FEEDS: list[tuple[str, str]] = [
    ("hackernews", "https://hnrss.org/frontpage?points=100"),
    # AI stories from HN, pre-filtered server-side: q=AI gates the topic,
    # points=50 gates popularity, link=comments makes each entry's link the
    # news.ycombinator.com/item?id=... discussion URL. Lets news_headlines
    # serve "popular AI HN stories with real discussion links" without the
    # model hand-building any Algolia query.
    ("hn-ai", "https://hnrss.org/newest?q=AI&points=50&link=comments"),
    ("tech", "https://techcrunch.com/feed/"),
    ("ai-research", "http://export.arxiv.org/rss/cs.AI"),
    ("world", "https://feeds.bbci.co.uk/news/world/rss.xml"),
]


def _feeds_file() -> Path:
    # Resolved per-call so tests can monkeypatch the env var between cases.
    return Path(os.environ.get("HOMUNCULUS_NEWS_FEEDS_FILE", "news_feeds.txt"))


def get_news_feeds() -> list[tuple[str, str]]:
    """Return the configured ``(label, url)`` feeds. Falls back to
    DEFAULT_FEEDS when the config file is absent or has no valid entries."""
    path = _feeds_file()
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return list(DEFAULT_FEEDS)
    feeds = _parse(raw)
    return feeds or list(DEFAULT_FEEDS)


def _parse(raw: str) -> list[tuple[str, str]]:
    feeds: list[tuple[str, str]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "|" not in line:
            continue
        label, url = (p.strip() for p in line.split("|", 1))
        if not url.lower().startswith(("http://", "https://")):
            continue
        feeds.append((label.lower() or "news", url))
    return feeds


def select_feeds(topic: str = "") -> list[tuple[str, str]]:
    """Feeds matching ``topic`` (substring of the label, case-insensitive).
    Returns all feeds when topic is empty or matches nothing, so the caller
    always gets something to fetch rather than an empty brief."""
    feeds = get_news_feeds()
    topic = (topic or "").strip().lower()
    if not topic:
        return feeds
    matched = [f for f in feeds if topic in f[0]]
    return matched or feeds
