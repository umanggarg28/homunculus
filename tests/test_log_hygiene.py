"""Log-hygiene regressions found in the 2026-07-08→10 trace sweep.

Three distinct failures, one theme — corruption and near-miss input
must be handled by the harness, not left to burn agent turns:

* events.rotate kept malformed lines forever, so a disk-full crash's
  NUL blob (2.4 MB) stayed pinned in the ledger for weeks;
* read_file raised a raw FileNotFoundError when the model dropped the
  directory from a known filename, and the model retried blind;
* the model leaked harmony markup into a tool NAME
  ("news_headlines<|channel|>commentary"), which dispatched as
  tool-does-not-exist instead of the obvious intended tool.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

import homunculus.events as events_mod
from tests.conftest import load_real_tool_submodule

filesystem = load_real_tool_submodule("filesystem")


def _iso(days_ago: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days_ago)).isoformat(timespec="seconds")


@pytest.fixture
def events_file(tmp_path, monkeypatch):
    path = tmp_path / "_events.jsonl"
    monkeypatch.setattr(events_mod, "_EVENTS_PATH", path)
    return path


def test_rotate_drops_malformed_interior_lines(events_file):
    """A NUL blob or truncated write from a crash must not survive
    rotation — no consumer can parse it, keeping it preserves
    corruption forever."""
    fresh = json.dumps({"ts": _iso(0), "event": "tool_call"})
    stale = json.dumps({"ts": _iso(30), "event": "tool_call"})
    lines = [stale, "\x00" * 512, '{"ts": "2026-06-05T01:00:00+00:00", "trunc', fresh]
    events_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    dropped = events_mod.rotate(keep_days=14)

    assert dropped == 3  # stale + both malformed
    kept = events_file.read_text(encoding="utf-8").splitlines()
    assert kept == [fresh]


def test_rotate_keeps_malformed_final_line(events_file):
    """The final line may be another service's append still in flight —
    a partial write there is not corruption and must survive."""
    stale = json.dumps({"ts": _iso(30), "event": "tool_call"})
    partial = '{"ts": "' + _iso(0)  # append in progress
    events_file.write_text(stale + "\n" + partial + "\n", encoding="utf-8")

    dropped = events_mod.rotate(keep_days=14)

    assert dropped == 1  # only the stale line
    kept = events_file.read_text(encoding="utf-8").splitlines()
    assert kept == [partial]


def test_read_file_missing_suggests_real_path(tmp_path, monkeypatch):
    """Live failure 2026-07-08: the model asked for
    'skill_quiz_coach.md' (the file lives at memory/…) and burned 4
    turns on raw FileNotFoundError. The error must carry the fix."""
    import homunculus.tools._helpers as helpers

    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "skill_quiz_coach.md").write_text("x", encoding="utf-8")
    monkeypatch.setattr(helpers, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)

    out = filesystem.read_file("skill_quiz_coach.md")

    assert out.startswith("ERROR")
    assert "memory/skill_quiz_coach.md" in out


def test_read_file_missing_no_lookalike(tmp_path, monkeypatch):
    import homunculus.tools._helpers as helpers

    monkeypatch.setattr(helpers, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)

    out = filesystem.read_file("nothing_like_this.md")

    assert out.startswith("ERROR")
    assert "Did you mean" not in out


def test_tool_name_harmony_leak_trimmed(monkeypatch):
    """Live failure 2026-07-10: the model called
    'news_headlines<|channel|>commentary'. The intended tool is
    unambiguous — dispatch must strip the markup and run it instead of
    burning a turn on tool-does-not-exist."""
    from homunculus import core
    import homunculus.tools as tools_module

    schema = {
        "type": "function",
        "function": {
            "name": "news_headlines",
            "description": "stub",
            "parameters": {"type": "object", "properties": {}},
        },
    }
    dispatched: list[tuple[str, dict]] = []
    monkeypatch.setattr(tools_module, "SCHEMAS", [schema], raising=False)
    monkeypatch.setattr(
        tools_module, "execute",
        lambda n, a: (dispatched.append((n, a)), "OK")[1],
        raising=False,
    )

    agent = core.Agent(memory=None)
    call = {
        "id": "c1",
        "type": "function",
        "function": {
            "name": "news_headlines<|channel|>commentary",
            "arguments": "{}",
        },
    }
    agent._dispatch_tool_calls([call], set(), {}, {}, [])

    assert dispatched, "leaked-name call was not dispatched at all"
    assert dispatched[0][0] == "news_headlines"
