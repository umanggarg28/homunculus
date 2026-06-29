"""rss_feed — watch an RSS/Atom feed and surface new entries.

The newsletter-distiller use case: point this at a feed, and on each run
it reports which entries are new since last time. Same harness-owned
change detection as github_profile (tools/watch.snapshot_diff) — the
model summarises the new posts, it never decides which are new by
re-reading two dumps.

Feeds are parsed with BeautifulSoup's XML mode (lxml backend, already a
dependency) rather than a feed-parsing library — the project is
framework-free, and we only need title/link/date per entry, which both
RSS 2.0 (<item>) and Atom (<entry>) expose plainly.
"""

from __future__ import annotations

from . import watch
from .web import _url_block_reason

import httpx

# Most "what's new" digests only care about the head of the feed; older
# entries scrolling off the bottom are not news. Capping also keeps the
# snapshot (and its diff) small and stable.
_MAX_ENTRIES = 15


def parse_feed_entries(text: str) -> list[dict[str, str]] | None:
    """Parse an RSS 2.0 or Atom document into a list of
    ``{"title", "link", "date", "summary", "comments"}`` dicts in feed-head
    order, or None if it isn't a recognisable feed. Shared by ``rss_feed``
    (change-diff) and the ``news_headlines`` tool so feed parsing lives in
    exactly one place.

    Atom carries the link in a ``href`` attribute, RSS in element text — both
    are handled. ``summary`` is the description/summary text (HN feeds put
    "Points: N" here); ``comments`` is the discussion URL when present. Entries
    with neither a title nor a link are dropped."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(text, "xml")
    items = soup.find_all("item")
    entries = items if items else soup.find_all("entry")
    if not entries:
        return None

    parsed: list[dict[str, str]] = []
    for entry in entries:
        title_el = entry.find("title")
        title = title_el.get_text(strip=True) if title_el else ""

        link_el = entry.find("link")
        if link_el is None:
            link = ""
        elif link_el.get("href"):  # Atom: <link href="...">
            href = link_el.get("href", "")
            # bs4 types a multi-valued attribute as list[str]; href is single.
            link = " ".join(href) if isinstance(href, list) else (href or "")
        else:  # RSS: <link>text</link>
            link = link_el.get_text(strip=True)

        date_el = (
            entry.find("pubDate")
            or entry.find("published")
            or entry.find("updated")
            or entry.find("date")
        )
        date = date_el.get_text(strip=True) if date_el else ""

        summary_el = entry.find("description") or entry.find("summary")
        summary = summary_el.get_text(strip=True) if summary_el else ""

        comments_el = entry.find("comments")
        comments = comments_el.get_text(strip=True) if comments_el else ""

        if not title and not link:
            continue
        parsed.append({
            "title": title or "(untitled)",
            "link": link,
            "date": date,
            "summary": summary,
            "comments": comments,
        })
    return parsed


def _build_summary(text: str) -> str | None:
    """Stable text rendering of the feed head, or None if it isn't a
    recognisable feed."""
    from bs4 import BeautifulSoup

    entries = parse_feed_entries(text)
    if entries is None:
        return None

    feed_title_el = BeautifulSoup(text, "xml").find("title")
    feed_title = feed_title_el.get_text(strip=True) if feed_title_el else "(feed)"

    lines = [
        f"feed: {feed_title}",
        f"entries: {len(entries)} (showing {min(len(entries), _MAX_ENTRIES)})",
    ]
    for entry in entries[:_MAX_ENTRIES]:
        # Date first so the diff groups by recency; body omitted so a publisher
        # editing post text doesn't read as a new post. Date truncated to keep
        # the snapshot stable against trailing tz/format drift.
        line = f"{entry['date'][:25]} · {entry['title']} — {entry['link']}".strip(" ·")
        lines.append(f"  {line}")
    return "\n".join(lines)


def rss_feed(name: str, url: str) -> str:
    """Fetch a feed and diff its entry list against the previous run."""
    if (reason := _url_block_reason(url)) is not None:
        return reason
    try:
        resp = httpx.get(
            url,
            timeout=30.0,
            follow_redirects=True,  # feeds commonly 301 to a CDN; body is the payload
            headers={"User-Agent": "Mozilla/5.0 (Homunculus AI assistant)"},
        )
    except httpx.HTTPError as e:
        return f"ERROR: feed request failed: {e}"
    if resp.status_code in {401, 403, 429}:
        return (
            f"BLOCKED: HTTP {resp.status_code} fetching feed {url}. "
            "The host blocks automated fetches; do not retry this URL."
        )
    if resp.status_code != 200:
        return f"ERROR: HTTP {resp.status_code} fetching feed {url}"

    summary = _build_summary(resp.text)
    if summary is None:
        return (
            f"ERROR: {url} did not parse as an RSS or Atom feed (no "
            "<item>/<entry> elements). Check the feed URL."
        )
    return watch.snapshot_diff(_feed_watch_name(name), summary, source=url)


def _feed_watch_name(name: str) -> str:
    return f"rss-{(name or '').strip().lower()}"
