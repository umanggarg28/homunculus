"""Change detection — the harness-owned half of every "tell me when it
changes" watcher.

The watcher pattern ("act only when something changed") is the backbone
of this agent's useful recurring deliveries. The comparison is the wrong
job for a weak LLM: re-reading two snapshots, it hallucinates
differences or misses real ones. So the harness owns the mechanics —
snapshot, line diff — and the model only judges whether a reported
change is *meaningful*.

`snapshot_diff(name, text, source)` is the reusable core: any tool that
can produce a stable text representation of "current state" gets
diff-against-last-run for free (watch_url feeds it raw page text;
github_profile feeds it a clean metrics summary). Snapshots live under
CACHE_DIR/watch/<name>.json (one file per watch, no TTL — a snapshot is
state, not a cache entry) and persist across ticks, restarts, rebuilds.
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


def _save_snapshot(name: str, source: str, text: str) -> None:
    path = _snapshot_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "source": source,
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


def snapshot_diff(name: str, text: str, source: str = "") -> str:
    """Diff `text` against the last snapshot stored under `name`.

    Returns FIRST SNAPSHOT (no baseline yet), NO CHANGE, or CHANGED +
    a capped unified diff. Advances the snapshot on first-run and on
    change. `source` is recorded for provenance and surfaced when it
    differs between runs (e.g. the watched URL moved). Callers must not
    pass an error string as `text` — that would poison the baseline;
    web-fetching callers check for ERROR:/BLOCKED: before calling.
    """
    name = (name or "").strip().lower()
    if not _NAME_RE.match(name):
        return (
            "ERROR: watch name must be a short slug (lowercase letters, "
            "digits, '-' or '_'), e.g. 'gh-stars-homunculus'. Reuse the "
            "exact same name on every run of the same watch."
        )

    previous = _load_snapshot(name)
    if previous is None:
        _save_snapshot(name, source, text)
        return (
            f"FIRST SNAPSHOT for '{name}' saved "
            f"({len(text.splitlines())} lines). Nothing to compare "
            "against yet — there is no change to report this run."
        )

    old_lines = previous["text"].splitlines()
    new_lines = text.splitlines()
    taken_at = previous.get("taken_at", "an earlier run")
    prev_source = previous.get("source", "")
    source_note = (
        ""
        if not source or prev_source == source
        else f" NOTE: snapshot was taken from a different source ({prev_source})."
    )

    if old_lines == new_lines:
        return f"NO CHANGE: '{name}' matches the snapshot from {taken_at}.{source_note}"

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
            f"[...diff truncated at {_MAX_DIFF_LINES} lines — content changed substantially]"
        ]
    _save_snapshot(name, source, text)
    return (
        f"CHANGED since {taken_at}: +{added}/-{removed} lines (snapshot updated).{source_note}\n"
        "Judge whether this change is MEANINGFUL before notifying — "
        "timestamps, counters and ads churn on every fetch.\n\n"
        + "\n".join(diff)
    )


def watch_url(name: str, url: str) -> str:
    text = web_fetch(url)
    if text.startswith(_CACHE_HIT_PREFIX):
        text = text[len(_CACHE_HIT_PREFIX):]
    if text.startswith(("ERROR:", "BLOCKED:")):
        # Never snapshot a failure — a transient 500 must not become the
        # baseline that makes the next healthy fetch look like a change.
        return text
    return snapshot_diff(name, text, source=url)
