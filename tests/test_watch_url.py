"""watch_url — the harness-owned half of the watcher pattern.

Every useful recurring delivery this agent runs (LeetCode, job alerts)
is secretly the same loop: fetch → compare to last time → act only on
change. Before this tool, the *compare* step belonged to the LLM, which
is exactly where a weak model hallucinates differences or misses real
ones. These tests pin the contract the skills rely on:

  - first call saves a baseline and explicitly says "no change to report"
  - identical content → NO CHANGE (the skill's signal to not notify)
  - changed content → unified diff + snapshot advances
  - fetch failures are passed through and NEVER become the baseline
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import load_real_tool_submodule

_helpers = load_real_tool_submodule("_helpers")
load_real_tool_submodule("web")
_watch = load_real_tool_submodule("watch")


@pytest.fixture()
def watch_env(tmp_path, monkeypatch):
    """Isolated snapshot dir + a stubbable fetch."""
    monkeypatch.setattr(_helpers, "CACHE_DIR", tmp_path)

    def set_page(text: str):
        monkeypatch.setattr(_watch, "web_fetch", lambda url: text)

    return set_page


def _snapshot_file(tmp_path, name: str) -> Path:
    return tmp_path / "watch" / f"{name}.json"


# ---- baseline behavior ------------------------------------------------


def test_first_run_saves_snapshot_and_reports_nothing_to_compare(watch_env, tmp_path):
    watch_env("line one\nline two")
    out = _watch.watch_url("my-watch", "https://example.com/page")
    assert out.startswith("FIRST SNAPSHOT")
    assert "no change to report" in out.lower()
    saved = json.loads(_snapshot_file(tmp_path, "my-watch").read_text())
    assert saved["text"] == "line one\nline two"
    assert saved["source"] == "https://example.com/page"


def test_identical_content_reports_no_change(watch_env):
    watch_env("same\ncontent")
    _watch.watch_url("w", "https://example.com")
    out = _watch.watch_url("w", "https://example.com")
    assert out.startswith("NO CHANGE")


def test_cache_hit_prefix_does_not_count_as_change(watch_env, monkeypatch):
    """web_fetch may serve from its disk cache; the marker prefix is
    transport detail, not content."""
    watch_env("stable page")
    _watch.watch_url("w", "https://example.com")
    monkeypatch.setattr(_watch, "web_fetch", lambda url: "[cache hit]\nstable page")
    out = _watch.watch_url("w", "https://example.com")
    assert out.startswith("NO CHANGE")


# ---- change detection ---------------------------------------------------


def test_changed_content_returns_diff_and_advances_snapshot(watch_env):
    watch_env("release v1.0\ndocs")
    _watch.watch_url("releases", "https://example.com")
    watch_env("release v1.1\ndocs")
    out = _watch.watch_url("releases", "https://example.com")
    assert out.startswith("CHANGED")
    assert "+release v1.1" in out
    assert "-release v1.0" in out
    # Snapshot advanced: same content again is NO CHANGE, not CHANGED.
    out2 = _watch.watch_url("releases", "https://example.com")
    assert out2.startswith("NO CHANGE")


def test_huge_diff_is_truncated(watch_env):
    watch_env("\n".join(f"old {i}" for i in range(300)))
    _watch.watch_url("big", "https://example.com")
    watch_env("\n".join(f"new {i}" for i in range(300)))
    out = _watch.watch_url("big", "https://example.com")
    assert "diff truncated" in out
    assert len(out.splitlines()) < 120


def test_different_url_same_name_is_flagged(watch_env):
    # Different source only shows up alongside a change — identical text
    # short-circuits to NO CHANGE before the note. Vary content too.
    watch_env("content a")
    _watch.watch_url("w", "https://example.com/a")
    watch_env("content b")
    out = _watch.watch_url("w", "https://example.com/b")
    assert "different source" in out


# ---- failure handling ---------------------------------------------------


def test_fetch_error_passes_through_and_is_not_snapshotted(watch_env, tmp_path):
    watch_env("ERROR: fetch failed: boom")
    out = _watch.watch_url("w", "https://example.com")
    assert out.startswith("ERROR:")
    assert not _snapshot_file(tmp_path, "w").exists()


def test_blocked_fetch_does_not_replace_healthy_baseline(watch_env):
    """A transient 403 must not become the baseline — otherwise the next
    healthy fetch reports the entire page as 'changed'."""
    watch_env("healthy page")
    _watch.watch_url("w", "https://example.com")
    watch_env("BLOCKED: HTTP 403 fetching https://example.com. ...")
    out = _watch.watch_url("w", "https://example.com")
    assert out.startswith("BLOCKED:")
    watch_env("healthy page")
    assert _watch.watch_url("w", "https://example.com").startswith("NO CHANGE")


def test_corrupt_snapshot_treated_as_first_run(watch_env, tmp_path):
    watch_env("content")
    _watch.watch_url("w", "https://example.com")
    _snapshot_file(tmp_path, "w").write_text("{not json", encoding="utf-8")
    out = _watch.watch_url("w", "https://example.com")
    assert out.startswith("FIRST SNAPSHOT")


# ---- input validation ---------------------------------------------------


@pytest.mark.parametrize("bad", ["", "  ", "UPPER CASE", "a/b", "../escape", "x" * 80])
def test_invalid_names_rejected(watch_env, bad):
    watch_env("content")
    out = _watch.watch_url(bad, "https://example.com")
    assert out.startswith("ERROR: watch name")
