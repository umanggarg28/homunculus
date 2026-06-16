"""News tool — top headlines from configured RSS/Atom feeds, as real links.

The weak model reliably fabricates news/discussion links when asked to "fetch
the feed, parse it, and build links" — it skips the fetch and invents plausible
URLs and IDs. This tool does the mechanical work (fetch + parse + link) across
the user's configured feeds and returns ready-to-use markdown so the model only
includes it verbatim. Sources are data (see news_feeds.py), so this one tool
covers any source the user lists — no per-site tool.

Feed parsing is shared with the rss_feed watcher (rss.parse_feed_entries), so
both tools handle RSS 2.0 and Atom the same way and there is one parser to
maintain. A feed that fails to fetch or parse is skipped, not fatal.
"""

from __future__ import annotations

import httpx

import news_feeds

from . import rss
from .web import _url_block_reason

# Connect fast (a dead feed shouldn't stall the brief) but allow a slower read:
# some feeds (hnrss.org, arXiv) routinely take >8s to render.
_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0)
_HEADERS = {"User-Agent": "Mozilla/5.0 (Homunculus AI assistant)"}


def news_headlines(topic: str = "", limit: int = 5) -> str:
    """Top headlines from the user's configured news feeds, as a markdown
    bullet list of REAL links. ``topic`` (optional) prefers feeds whose label
    contains it (e.g. "tech", "ai", "world", "hackernews"). Returns
    ``NEWS_UNAVAILABLE: ...`` on total failure so the caller omits the section
    rather than inventing links."""
    limit = max(1, min(int(limit), 15))
    feeds = news_feeds.select_feeds(topic)
    if not feeds:
        return "NEWS_UNAVAILABLE: no feeds configured. Omit the news section."

    by_label: dict[str, list[tuple[str, str]]] = {}
    order: list[str] = []
    for label, url in feeds:
        if _url_block_reason(url) is not None:
            continue
        pairs = _fetch_feed(url)
        if not pairs:
            continue
        if label not in by_label:
            by_label[label] = []
            order.append(label)
        by_label[label].extend(pairs)

    if not order:
        return (
            "NEWS_UNAVAILABLE: every configured feed failed to fetch or parse. "
            "Omit the news section."
        )

    # Round-robin across feeds so a single chatty source can't dominate the
    # brief — take one from each label in turn until we hit the limit.
    lines: list[str] = []
    seen: set[str] = set()
    while len(lines) < limit and any(by_label[l] for l in order):
        for label in order:
            if not by_label[label]:
                continue
            title, link = by_label[label].pop(0)
            if link in seen:
                continue
            seen.add(link)
            lines.append(f"- [{title}]({link})")
            if len(lines) >= limit:
                break
    return "\n".join(lines)


def _fetch_feed(url: str) -> list[tuple[str, str]]:
    """Fetch one feed and return ``(title, link)`` pairs with real http links.
    Returns [] on any failure so the caller skips this source."""
    try:
        r = httpx.get(url, timeout=_TIMEOUT, headers=_HEADERS, follow_redirects=True)
        r.raise_for_status()
    except httpx.HTTPError:
        return []
    entries = rss.parse_feed_entries(r.text) or []
    pairs: list[tuple[str, str]] = []
    for e in entries:
        title, link = e.get("title", ""), e.get("link", "")
        if title and link.lower().startswith(("http://", "https://")):
            pairs.append((title, link))
    return pairs
