"""WorldStateStore + NextTickStore + ReflectionStore — extracted from
Memory god class (Bundle 2 #2).

All three are simple single-file stores. Tests pin both the
standalone behavior and the Memory-property accessor wiring.
"""

from __future__ import annotations

from pathlib import Path

from stores import NextTickStore, ReflectionStore, WorldStateStore


# ---- WorldStateStore --------------------------------------------------


def test_world_state_read_empty_returns_empty_dict(tmp_path: Path) -> None:
    ws = WorldStateStore(tmp_path)
    assert ws.read() == {}


def test_world_state_update_merges_and_stamps_updated_at(tmp_path: Path) -> None:
    ws = WorldStateStore(tmp_path)
    out = ws.update({"focus": "task A", "step": 1})
    assert out["focus"] == "task A"
    assert out["step"] == 1
    assert "updated_at" in out


def test_world_state_update_preserves_unrelated_keys(tmp_path: Path) -> None:
    """Update is a merge, not a replace."""
    ws = WorldStateStore(tmp_path)
    ws.update({"focus": "A", "step": 1})
    ws.update({"step": 2})
    out = ws.read()
    assert out["focus"] == "A"  # preserved
    assert out["step"] == 2     # overwritten


def test_world_state_clear_removes_file(tmp_path: Path) -> None:
    ws = WorldStateStore(tmp_path)
    ws.update({"focus": "A"})
    ws.clear()
    assert ws.read() == {}
    assert not ws.path.exists()


def test_world_state_clear_on_empty_is_noop(tmp_path: Path) -> None:
    ws = WorldStateStore(tmp_path)
    ws.clear()  # must not raise even though file doesn't exist


def test_world_state_malformed_file_returns_empty(tmp_path: Path) -> None:
    """A corrupted file shouldn't crash readers — degrade to empty."""
    ws = WorldStateStore(tmp_path)
    ws.path.write_text("not-json", encoding="utf-8")
    assert ws.read() == {}


# ---- NextTickStore ----------------------------------------------------


def test_next_tick_set_then_peek_returns_value(tmp_path: Path) -> None:
    nt = NextTickStore(tmp_path)
    nt.set("2026-06-08T07:00:00")
    assert nt.peek() == "2026-06-08T07:00:00"


def test_next_tick_peek_does_not_consume(tmp_path: Path) -> None:
    nt = NextTickStore(tmp_path)
    nt.set("2026-06-08T07:00:00")
    nt.peek()
    nt.peek()
    assert nt.peek() == "2026-06-08T07:00:00"


def test_next_tick_pop_consumes(tmp_path: Path) -> None:
    nt = NextTickStore(tmp_path)
    nt.set("2026-06-08T07:00:00")
    assert nt.pop() == "2026-06-08T07:00:00"
    assert nt.pop() is None
    assert not nt.path.exists()


def test_next_tick_empty_returns_none(tmp_path: Path) -> None:
    nt = NextTickStore(tmp_path)
    assert nt.peek() is None
    assert nt.pop() is None


# ---- ReflectionStore --------------------------------------------------


def test_reflection_mark_then_last_date(tmp_path: Path) -> None:
    r = ReflectionStore(tmp_path)
    assert r.last_date() is None
    r.mark("2026-06-07")
    assert r.last_date() == "2026-06-07"


def test_reflection_mark_overwrites(tmp_path: Path) -> None:
    """A second mark for a different day must replace, not append."""
    r = ReflectionStore(tmp_path)
    r.mark("2026-06-06")
    r.mark("2026-06-07")
    assert r.last_date() == "2026-06-07"


# ---- Memory wiring ----------------------------------------------------


def test_memory_world_state_returns_store(tmp_path: Path) -> None:
    from memory import Memory
    mem = Memory(tmp_path)
    assert isinstance(mem.world_state, WorldStateStore)
    assert mem.world_state is mem.world_state  # lazy singleton


def test_memory_next_tick_returns_store(tmp_path: Path) -> None:
    from memory import Memory
    mem = Memory(tmp_path)
    assert isinstance(mem.next_tick, NextTickStore)
    assert mem.next_tick is mem.next_tick


def test_memory_reflection_returns_store(tmp_path: Path) -> None:
    from memory import Memory
    mem = Memory(tmp_path)
    assert isinstance(mem.reflection, ReflectionStore)
    assert mem.reflection is mem.reflection


def test_memory_sub_stores_share_root(tmp_path: Path) -> None:
    """All sub-stores must root at Memory.root so they all touch the
    same agent dir — otherwise a reset on one wouldn't reset the others."""
    from memory import Memory
    mem = Memory(tmp_path)
    assert mem.world_state.root == tmp_path
    assert mem.next_tick.root == tmp_path
    assert mem.reflection.root == tmp_path
