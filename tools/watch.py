"""watch_url — deterministic change detection for recurring watchers.

The watcher pattern ("tell me only when something changed") is the
backbone of every useful recurring delivery this agent runs. The LLM is
the wrong place to do the comparison: a weak model re-reading two page
dumps will hallucinate differences or miss real ones. So the harness
owns the mechanics — fetch, snapshot, line diff — and the model only
judges whether a reported change is *meaningful*.

Snapshots live under CACHE_DIR/watch/<name>.json (one file per watch,
no TTL — a snapshot is state, not a cache entry) and persist across
ticks, restarts, and rebuilds.
"""

from __future__ import annotations

import difflib
import json
import re
import time

from . import _helpers
from .web import web_fetch

# Watch names are stable identifiers chosen by the agent ("gh-stars",
# "substack-feed"). Slug-only keeps them safe as filenames.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9\-_]{0,63}$")

# A unified diff longer than this is no longer a "change report" — it
# means the page was rewritten (or the watch is too broad). Cap it so
# one watcher can't flood the model's context.
_MAX_DIFF_LINES = 80

_CACHE_HIT_PREFIX = "[cache hit]\n"


def _snapshot_path(name: str):
    # CACHE_DIR is looked up through the module (not imported as a
    # value) so tests can repoint it at tmp_path.
    return _helpers.CACHE_DIR / "watch" / f"{name}.json"


def _save_snapshot(name: str, url: str, text: str) -> None:
    path = _snapshot_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "url": url,
        "taken_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        "text": text,
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _load_snapshot(name: str) -> dict | None:
    path = _snapshot_path(name)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None  # corrupt snapshot — treat as first run
    return data if isinstance(data.get("text"), str) else None


def watch_url(name: str, url: str) -> str:
    name = (name or "").strip().lower()
    if not _NAME_RE.match(name):
        return (
            "ERROR: watch name must be a short slug (lowercase letters, "
            "digits, '-' or '_'), e.g. 'gh-stars-homunculus'. Reuse the "
            "exact same name on every run of the same watch."
        )

    text = web_fetch(url)
    if text.startswith(_CACHE_HIT_PREFIX):
        text = text[len(_CACHE_HIT_PREFIX):]
    if text.startswith(("ERROR:", "BLOCKED:")):
        # Never snapshot a failure — a transient 500 must not become the
        # baseline that makes the next healthy fetch look like a change.
        return text

    previous = _load_snapshot(name)
    if previous is None:
        _save_snapshot(name, url, text)
        return (
            f"FIRST SNAPSHOT for '{name}' saved "
            f"({len(text.splitlines())} lines from {url}). Nothing to "
            "compare against yet — there is no change to report this run."
        )

    old_lines = previous["text"].splitlines()
    new_lines = text.splitlines()
    taken_at = previous.get("taken_at", "an earlier run")
    url_note = (
        ""
        if previous.get("url") == url
        else f" NOTE: snapshot was taken from a different URL ({previous.get('url')})."
    )

    if old_lines == new_lines:
        return f"NO CHANGE: '{name}' matches the snapshot from {taken_at}.{url_note}"

    diff = list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"snapshot ({taken_at})",
            tofile="now",
            lineterm="",
            n=1,
        )
    )
    added = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
    if len(diff) > _MAX_DIFF_LINES:
        diff = diff[:_MAX_DIFF_LINES] + [
            f"[...diff truncated at {_MAX_DIFF_LINES} lines — page changed substantially]"
        ]
    _save_snapshot(name, url, text)
    return (
        f"CHANGED since {taken_at}: +{added}/-{removed} lines (snapshot updated).{url_note}\n"
        "Judge whether this change is MEANINGFUL before notifying — "
        "timestamps, view counters and ads churn on every fetch.\n\n"
        + "\n".join(diff)
    )
