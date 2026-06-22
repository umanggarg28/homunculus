"""News tool — top headlines from configured RSS/Atom feeds, as real links.

The weak model reliably fabricates news/discussion links when asked to "fetch
the feed, parse it, and build links" — it skips the fetch and invents plausible
URLs and IDs. This tool does the mechanical work (fetch + parse + rank + link)
across the user's configured feeds and returns ready-to-use markdown so the
model only includes it verbatim. Sources are data (see news_feeds.py), so this
one tool covers any source the user lists — no per-site tool.

Ranking, not round-robin. Feeds expose uneven signals: Hacker News carries real
engagement ("Points: N" in the entry body); most press/research feeds are only
reverse-chronological. So we rank the combined pool on the signals that exist —
engagement where present, recency as the universal axis — drop the stale tail,
and cap per source so the brief stays a broad mix instead of one source's
firehose. This is why "top" means top, not merely "newest from each feed".

Feed parsing is shared with the rss_feed watcher (rss.parse_feed_entries).
A feed that fails to fetch or parse is skipped, not fatal.
"""

from __future__ import annotations

import re
from datetime import datetime, UTC
from email.utils import parsedate_to_datetime

import httpx

from homunculus import news_feeds

from . import rss
from .web import _url_block_reason

_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0)
_HEADERS = {"User-Agent": "Mozilla/5.0 (Homunculus AI assistant)"}

# Items older than this are dropped from a "top headlines" view — a two-day-old
# article isn't a headline. HN items are exempt: a high-point story stays
# relevant past 48h, and the points filter already gates quality.
_FRESH_HOURS = 48
# No single source may take more than this many slots, so the mix stays broad
# even when one feed (HN) dominates on raw engagement score.
_PER_SOURCE_CAP = 2
# Points at which HN engagement saturates to a full score — above this, more
# points don't meaningfully raise rank.
_POINTS_SATURATION = 300

_POINTS_RE = re.compile(r"Points:\s*(\d+)", re.IGNORECASE)


def news_headlines(topic: str = "", limit: int = 5) -> str:
    """Top headlines from the user's configured news feeds, as a markdown
    bullet list of REAL links, ranked by engagement + recency. ``topic``
    (optional) prefers feeds whose label contains it (e.g. "tech", "ai",
    "world", "hackernews"). Returns ``NEWS_UNAVAILABLE: ...`` on total failure
    so the caller omits the section rather than inventing links."""
    limit = max(1, min(int(limit), 15))
    feeds = news_feeds.select_feeds(topic)
    if not feeds:
        return "NEWS_UNAVAILABLE: no feeds configured. Omit the news section."

    items: list[dict] = []
    for label, url in feeds:
        if _url_block_reason(url) is not None:
            continue
        items.extend(_fetch_feed(label, url))

    if not items:
        return (
            "NEWS_UNAVAILABLE: every configured feed failed to fetch or parse. "
            "Omit the news section."
        )

    chosen = _rank_and_select(items, limit)
    if not chosen:
        return "NEWS_UNAVAILABLE: no fresh headlines right now. Omit the news section."
    return "\n".join(f"- [{it['title']}]({it['link']})" for it in chosen)


def _fetch_feed(label: str, url: str) -> list[dict]:
    """Fetch one feed and return scored item dicts with real http links.
    Returns [] on any failure so the caller skips this source."""
    try:
        r = httpx.get(url, timeout=_TIMEOUT, headers=_HEADERS, follow_redirects=True)
        r.raise_for_status()
    except httpx.HTTPError:
        return []
    out: list[dict] = []
    for e in rss.parse_feed_entries(r.text) or []:
        title, link = e.get("title", ""), e.get("link", "")
        if not title or not link.lower().startswith(("http://", "https://")):
            continue
        out.append({
            "label": label,
            "title": title,
            "link": link,
            "points": _points(e.get("summary", "")),
            "age_hours": _age_hours(e.get("date", "")),
        })
    return out


def _rank_and_select(items: list[dict], limit: int) -> list[dict]:
    """Drop the stale tail, then pick the highest-scoring items with a
    per-source cap so the result is a ranked, broad mix."""
    fresh = [it for it in items if _is_fresh(it)]
    pool = fresh or items  # never return empty just because everything is old

    # Dedup by link, keeping the first (highest-scoring after the sort below).
    pool.sort(key=_score, reverse=True)
    seen: set[str] = set()
    chosen: list[dict] = []
    per_source: dict[str, int] = {}
    for it in pool:
        if it["link"] in seen:
            continue
        if per_source.get(it["label"], 0) >= _PER_SOURCE_CAP:
            continue
        seen.add(it["link"])
        per_source[it["label"]] = per_source.get(it["label"], 0) + 1
        chosen.append(it)
        if len(chosen) >= limit:
            break

    # If the per-source cap left us short of `limit` (few sources), relax it
    # and top up by score so we still fill the brief.
    if len(chosen) < limit:
        for it in pool:
            if it["link"] in seen:
                continue
            seen.add(it["link"])
            chosen.append(it)
            if len(chosen) >= limit:
                break
    return chosen


def _score(it: dict) -> float:
    """Higher = more deserving of a slot. Engagement (HN points) dominates when
    present; recency is the universal axis everything shares."""
    recency = max(0.0, 1.0 - (it["age_hours"] / (_FRESH_HOURS * 2)))
    if it["points"] is not None:
        engagement = min(it["points"] / _POINTS_SATURATION, 1.0)
        return 0.6 * engagement + 0.4 * recency
    # Chrono feeds (no engagement signal): ranked purely on freshness, and kept
    # below a strong HN story but able to beat a weak/old one.
    return 0.5 * recency


def _is_fresh(it: dict) -> bool:
    if it["points"] is not None:
        return True  # HN points already gate quality; let strong stories persist
    return it["age_hours"] <= _FRESH_HOURS


def _points(summary: str) -> int | None:
    m = _POINTS_RE.search(summary or "")
    return int(m.group(1)) if m else None


def _age_hours(date_str: str) -> float:
    """Hours since publication. Unknown/garbage dates → a neutral mid value so
    the item neither wins nor is dropped on recency alone."""
    if not date_str:
        return _FRESH_HOURS / 2
    dt = None
    try:  # RSS uses RFC-822: "Tue, 16 Jun 2026 20:12:05 +0000"
        dt = parsedate_to_datetime(date_str)
    except (TypeError, ValueError, IndexError):
        try:  # Atom uses ISO-8601: "2026-06-16T20:12:05Z"
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except ValueError:
            return _FRESH_HOURS / 2
    if dt is None:
        return _FRESH_HOURS / 2
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    delta = datetime.now(UTC) - dt
    return max(0.0, delta.total_seconds() / 3600.0)
