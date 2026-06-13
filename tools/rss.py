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


def _entry_text(entry, link_tag_name: str) -> str | None:
    """One stable line per entry: 'YYYY-MM-DD · Title — link'.

    Date first so the diff groups by recency; we deliberately omit the
    body so a publisher editing post text doesn't read as a new post."""
    title_el = entry.find("title")
    title = title_el.get_text(strip=True) if title_el else "(untitled)"

    link_el = entry.find(link_tag_name)
    if link_el is None:
        link = ""
    elif link_el.get("href"):  # Atom: <link href="...">
        link = link_el.get("href", "")
    else:  # RSS: <link>text</link>
        link = link_el.get_text(strip=True)

    date_el = (
        entry.find("pubDate")
        or entry.find("published")
        or entry.find("updated")
        or entry.find("date")
    )
    date = date_el.get_text(strip=True)[:25] if date_el else ""

    if title == "(untitled)" and not link:
        return None
    return f"{date} · {title} — {link}".strip(" ·")


def _build_summary(text: str) -> str | None:
    """Stable text rendering of the feed head, or None if it isn't a
    recognisable feed."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(text, "xml")
    # RSS uses <item>; Atom uses <entry>. Atom <link> carries the URL in
    # an href attribute, RSS in element text — _entry_text handles both.
    items = soup.find_all("item")
    entries = items if items else soup.find_all("entry")
    if not entries:
        return None

    channel_title_el = soup.find("title")
    feed_title = channel_title_el.get_text(strip=True) if channel_title_el else "(feed)"

    lines = [f"feed: {feed_title}", f"entries: {len(entries)} (showing {min(len(entries), _MAX_ENTRIES)})"]
    for entry in entries[:_MAX_ENTRIES]:
        line = _entry_text(entry, link_tag_name="link")
        if line:
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
