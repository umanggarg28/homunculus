"""Tests for Memory: search, load_core_block, and basic CRUD.

All tests use a tmpdir — no production workspace is touched.
"""

import time
from pathlib import Path

import pytest

from homunculus.memory import Memory


@pytest.fixture()
def mem(tmp_path: Path) -> Memory:
    return Memory(tmp_path / "memory")


def _write_entry(mem: Memory, slug: str, type_: str, name: str, body: str) -> None:
    mem.root.mkdir(parents=True, exist_ok=True)
    path = mem.root / f"{type_}_{slug}.md"
    path.write_text(
        f"---\nname: {name}\ndescription: test\ntype: {type_}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    mem._upsert_index_entry(name, "test", path.name)


# ---------------------------------------------------------------------------
# Memory.search
# ---------------------------------------------------------------------------

class TestSearch:
    def test_returns_matching_snippet(self, mem):
        _write_entry(mem, "leetcode", "project", "LeetCode Tracker", "Daily problem delivery at 9am.")
        result = mem.search("leetcode problem", limit=3)
        assert "LeetCode" in result or "leetcode" in result

    def test_vague_single_short_term_returns_empty(self, mem):
        _write_entry(mem, "name", "user", "User Name", "User is Umang.")
        # Single 2-char term — below the 5-char threshold, below 2-term threshold.
        result = mem.search("hi", limit=3)
        assert result == ""

    def test_no_match_returns_empty(self, mem):
        _write_entry(mem, "food", "user", "Food Prefs", "Loves biryani.")
        result = mem.search("kubernetes deployment strategy", limit=3)
        assert result == ""

    def test_skips_index_and_readme(self, mem):
        # Index and README should never appear as search results.
        _write_entry(mem, "test", "project", "Test Entry", "kubernetes cluster setup")
        result = mem.search("kubernetes", limit=5)
        assert "MEMORY.md" not in result
        assert "README.md" not in result

    def test_stale_marker_on_old_entry(self, mem, monkeypatch):
        _write_entry(mem, "old", "reference", "Old Ref", "some old reference content here.")
        path = mem.root / "reference_old.md"
        # Backdate the file 35 days.
        old_ts = time.time() - 35 * 86400
        import os
        os.utime(path, (old_ts, old_ts))
        result = mem.search("old reference content", limit=3)
        assert "stale" in result

    def test_limit_respected(self, mem):
        for i in range(5):
            _write_entry(mem, f"item{i}", "project", f"Project {i}", f"unique keyword zephyr item number {i}")
        result = mem.search("zephyr", limit=2)
        # With limit=2 we should get at most 2 ## headers.
        assert result.count("##") <= 2


# ---------------------------------------------------------------------------
# Memory.load_core_block
# ---------------------------------------------------------------------------

class TestLoadCoreBlock:
    def test_returns_user_and_feedback(self, mem):
        _write_entry(mem, "name", "user", "User Name", "User is Umang.")
        _write_entry(mem, "no_emojis", "feedback", "No Emojis", "Never use emojis in replies.")
        block = mem.load_core_block()
        assert "Umang" in block
        assert "emojis" in block.lower()

    def test_excludes_project_and_reference(self, mem):
        _write_entry(mem, "leetcode", "project", "LeetCode", "Daily at 9am.")
        _write_entry(mem, "obsidian", "reference", "Obsidian", "vault at ~/Notes.")
        block = mem.load_core_block()
        # project and reference entries should not appear in the core block.
        assert "9am" not in block
        assert "vault" not in block

    def test_truncates_long_bodies(self, mem):
        long_body = "word " * 200  # ~1000 chars
        _write_entry(mem, "verbose", "user", "Verbose User", long_body)
        block = mem.load_core_block(max_chars_per_entry=100)
        assert len(block) < 500  # truncated, not the full body

    def test_empty_when_no_relevant_memories(self, mem):
        _write_entry(mem, "proj", "project", "A Project", "some project detail")
        block = mem.load_core_block()
        assert block == ""

    def test_respects_max_per_type(self, mem):
        for i in range(5):
            _write_entry(mem, f"user{i}", "user", f"User Fact {i}", f"fact {i}")
        block = mem.load_core_block(max_per_type=2)
        # Should include at most 2 user entries (plus the bolded label prefix).
        assert block.count("**") <= 4  # 2 entries × 1 opening bold each


# ---------------------------------------------------------------------------
# Memory.remember / forget round-trip
# ---------------------------------------------------------------------------

class TestRememberForget:
    def test_remember_creates_file(self, mem):
        result = mem.remember("Test Entry", "a test", "project", "some body text")
        assert "Saved" in result
        assert (mem.root / "project_test_entry.md").exists()

    def test_remember_upserts_index(self, mem):
        mem.remember("My Fact", "desc", "user", "body text")
        index = mem.index_path.read_text()
        assert "My Fact" in index

    def test_forget_removes_file_and_index(self, mem):
        mem.remember("Temp Fact", "desc", "feedback", "temp body")
        mem.forget("feedback_temp_fact")
        assert not (mem.root / "feedback_temp_fact.md").exists()
        index = mem.index_path.read_text()
        assert "Temp Fact" not in index

    def test_forget_by_human_name(self, mem):
        mem.remember("Human Name", "desc", "user", "some body")
        result = mem.forget("Human Name")
        assert "Forgot" in result

    def test_forget_nonexistent_is_idempotent(self, mem):
        result = mem.forget("nonexistent_memory")
        assert "not found" in result.lower() or "forgot" in result.lower()

    # ------------------------------------------------------------------
    # Type-prefix normalization
    # ------------------------------------------------------------------

    def test_strips_single_type_prefix_from_name(self, mem):
        # agent passes name="project_my_tracker", type="project"
        # should create project_my_tracker.md, NOT project_project_my_tracker.md
        mem.remember("project_my_tracker", "desc", "project", "body")
        assert (mem.root / "project_my_tracker.md").exists()
        assert not (mem.root / "project_project_my_tracker.md").exists()

    def test_strips_double_type_prefix_from_name(self, mem):
        # Handles the triple-prefix corruption case
        mem.remember("project_project_delivered_problems", "desc", "project", "body")
        assert (mem.root / "project_delivered_problems.md").exists()
        assert not (mem.root / "project_project_project_delivered_problems.md").exists()

    def test_clean_name_unchanged(self, mem):
        # A name with no type prefix is not modified
        mem.remember("delivered_problems", "desc", "project", "body")
        assert (mem.root / "project_delivered_problems.md").exists()

    # ------------------------------------------------------------------
    # Failure-state guard
    # ------------------------------------------------------------------

    def test_rejects_failure_state_body(self, mem):
        result = mem.remember("bad_note", "desc", "project", "Unable to locate the tracker file.")
        assert "ERROR" in result
        assert not (mem.root / "project_bad_note.md").exists()

    def test_rejects_uncertainty_body(self, mem):
        result = mem.remember("todo", "desc", "project", "Need to find where the list is stored.")
        assert "ERROR" in result

    def test_accepts_normal_body(self, mem):
        result = mem.remember("good_note", "desc", "project", "Delivered Two Sum on 2026-05-20.")
        assert "Saved" in result
