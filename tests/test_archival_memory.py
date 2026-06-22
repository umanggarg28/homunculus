"""Item 6 of the robustness plan — Letta-style archival memory.

API mirrors Letta:
  archival_memory_insert(content, tags=[]) -> token
  archival_memory_search(query, k=5)       -> formatted snippets

Storage = SQLite (memory.db archival_memory table). Same Gemini
embeddings the rest of memory.py uses. Tested with the embedding
fetch stubbed out so no network is required.
"""

from pathlib import Path

import homunculus.memory as memory_module


def _make_memory(tmp_path: Path) -> memory_module.Memory:
    """Build a Memory instance against an isolated tmpdir with no network."""
    m = memory_module.Memory(tmp_path)
    # Stub embeddings so tests don't hit the network. None means "no vec",
    # which exercises the fallback (chronological order) path.
    m._embed = lambda _t: None  # type: ignore[assignment]
    return m


def test_insert_returns_arch_prefixed_token(tmp_path):
    m = _make_memory(tmp_path)
    token = m.archival.insert("hello world", tags=["greeting"])
    assert isinstance(token, str)
    assert token.startswith("arch_")
    # Token format: arch_YYYYMMDDHHMMSS_<6char>
    parts = token.split("_")
    assert len(parts) == 3
    assert len(parts[1]) == 14  # YYYYMMDDHHMMSS
    assert len(parts[2]) == 6


def test_inserted_content_is_retrievable(tmp_path):
    m = _make_memory(tmp_path)
    token = m.archival.insert(
        "The quick brown fox jumps over the lazy dog.",
        tags=["pangram"],
    )
    result = m.archival.search("anything", k=5)
    assert token in result
    assert "quick brown fox" in result
    assert "pangram" in result


def test_empty_archive_returns_friendly_message(tmp_path):
    m = _make_memory(tmp_path)
    result = m.archival.search("anything")
    assert "empty" in result.lower()


def test_search_returns_top_k(tmp_path):
    m = _make_memory(tmp_path)
    for i in range(7):
        m.archival.insert(f"entry number {i}", tags=[f"t{i}"])
    result = m.archival.search("entry", k=3)
    # Three entries means three "── arch_..." headers
    assert result.count("── arch_") == 3


def test_long_content_preview_is_truncated_with_hint(tmp_path):
    m = _make_memory(tmp_path)
    huge = "X" * 5000
    token = m.archival.insert(huge, tags=["large"])
    result = m.archival.search("anything", k=1)
    assert token in result
    # Preview is trimmed; the hint mentions remaining chars and the token
    assert "+" in result and "chars" in result and "full content stays" in result


def test_insert_includes_tags_in_search_output(tmp_path):
    m = _make_memory(tmp_path)
    m.archival.insert("content with tags", tags=["alpha", "beta"])
    result = m.archival.search("content")
    assert "alpha" in result and "beta" in result


def test_insert_handles_no_tags(tmp_path):
    m = _make_memory(tmp_path)
    token = m.archival.insert("no tags here")
    result = m.archival.search("anything")
    assert token in result
    # No "[tags: ...]" should appear for tag-less entries
    # (the formatter omits the tag clause when the tags field is empty)
    assert "[tags: ]" not in result


def test_archival_storage_survives_new_memory_instance(tmp_path):
    """Tokens written through one Memory instance must be readable from another
    pointing at the same root — verifying SQLite-backed persistence."""
    m1 = _make_memory(tmp_path)
    token = m1.archival.insert("persistent content")
    # Fresh instance, same dir
    m2 = _make_memory(tmp_path)
    result = m2.archival.search("anything")
    assert token in result
    assert "persistent content" in result
