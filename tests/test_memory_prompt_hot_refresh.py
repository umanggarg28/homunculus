"""The system prompt's memory sections refresh when memory changes.

Regression: the core block + index were baked into _base_system_prompt at
Agent construction, so a long-lived agent (the web chat session) never saw
new memories in its index — a remember() in chat was invisible to the very
next turn's prompt until a process restart. AGENTS.md had mtime-cached
hot-reload; memory didn't. The change signal is MEMORY.md's mtime: every
remember()/forget() rewrites the index under the memory lock.
"""

from __future__ import annotations

import os

from homunculus.core import Agent
from homunculus.memory import Memory


def _mem(tmp_path) -> Memory:
    return Memory(tmp_path / "memory")


def test_new_memory_appears_in_next_turn_prompt(tmp_path):
    mem = _mem(tmp_path)
    agent = Agent(memory=mem)

    before = agent._current_system_prompt()
    assert "teal-preference" not in before

    mem.remember(
        name="teal-preference",
        description="favorite color is teal",
        type="user",
        body="The user's favorite color is teal.",
    )
    # Force a distinct mtime even on coarse-granularity filesystems.
    os.utime(mem.index_path, (os.path.getmtime(mem.index_path) + 2,) * 2)

    after = agent._current_system_prompt()
    assert "teal-preference" in after, (
        "a memory written mid-session must appear in the next turn's index"
    )


def test_forgotten_memory_leaves_the_prompt(tmp_path):
    mem = _mem(tmp_path)
    mem.remember(
        name="stale-fact", description="soon gone", type="project", body="x",
    )
    agent = Agent(memory=mem)
    assert "stale-fact" in agent._current_system_prompt()

    mem.forget("stale-fact")
    os.utime(mem.index_path, (os.path.getmtime(mem.index_path) + 2,) * 2)
    assert "stale-fact" not in agent._current_system_prompt()


def test_unchanged_memory_serves_from_cache(tmp_path):
    mem = _mem(tmp_path)
    mem.remember(name="pinned", description="d", type="user", body="user fact")
    agent = Agent(memory=mem)
    agent._current_system_prompt()

    calls: list[int] = []
    original = mem.load_index
    mem.load_index = lambda **kw: calls.append(1) or original(**kw)  # type: ignore[method-assign]
    agent._current_system_prompt()
    agent._current_system_prompt()
    assert calls == [], "an unchanged index must not be re-read every turn"


def test_memory_block_precedes_volatile_date_line(tmp_path):
    """Cache-prefix discipline: memory sections are stable content and must
    stay before the volatile suffix."""
    mem = _mem(tmp_path)
    mem.remember(name="pinned", description="d", type="user", body="user fact")
    agent = Agent(memory=mem)
    prompt = agent._current_system_prompt()
    assert prompt.index("# Memory index") < prompt.index("Current date/time:")


def test_upsert_preserves_hand_written_index_notes(tmp_path):
    """MEMORY.md non-entry lines survive remember() exactly as they survive
    forget() — the two paths used to disagree (upsert reset the header)."""
    mem = _mem(tmp_path)
    mem.remember(name="first", description="d1", type="user", body="b1")

    note = "> Curator's note: entries below are auto-managed."
    raw = mem.index_path.read_text(encoding="utf-8")
    mem.index_path.write_text(note + "\n\n" + raw, encoding="utf-8")

    mem.remember(name="second", description="d2", type="user", body="b2")
    after = mem.index_path.read_text(encoding="utf-8")
    assert note in after, "upsert dropped a hand-written index line"
    assert "(memory/user_first.md)" in after
    assert "(memory/user_second.md)" in after
