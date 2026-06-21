"""T1.2 — skill auto-refinement on failure.

When a task fails, the matching skill_*.md gets a "Watch out:" note
appended with the failure reason. Mirrors Hermes Agent's
evaluate→refine→retrieve loop.
"""

import importlib.util
from datetime import datetime
from pathlib import Path


# Load the pure-python module without going through tools/__init__.py
# (which has package-relative imports that fail under conftest stubs).
_SR_PATH = Path(__file__).parent.parent / "homunculus" / "tools" / "_skill_refiner.py"
_spec = importlib.util.spec_from_file_location("skill_refiner_real", _SR_PATH)
assert _spec and _spec.loader
_sr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sr)

update_skill_on_failure = _sr.update_skill_on_failure
_find_matching_skill = _sr._find_matching_skill
_tokens = _sr._tokens
MAX_WATCH_OUTS_PER_SKILL = _sr.MAX_WATCH_OUTS_PER_SKILL


# ── _tokens — the scoring fuel ──


def test_tokenizer_drops_short_filler():
    assert _tokens("the-and-task-skill") == set()


def test_tokenizer_drops_pure_digits():
    toks = _tokens("daily-leetcode-150-problem-at-9-am-ist")
    # 150 and 9 should NOT be in the token set
    assert "150" not in toks and "9" not in toks
    # Meaningful tokens should be present
    assert "leetcode" in toks
    assert "problem" in toks


def test_tokenizer_is_case_insensitive():
    assert _tokens("DAILY-LeetCode") == _tokens("daily-leetcode")


# ── _find_matching_skill — heuristic match ──


def _write_skill(memory_dir: Path, name: str, body: str = "# x\n") -> Path:
    p = memory_dir / f"{name}.md"
    p.write_text(body, encoding="utf-8")
    return p


def test_finds_skill_with_token_overlap(tmp_path: Path):
    _write_skill(tmp_path, "skill_deliver_daily_leetcode")
    result = _find_matching_skill("daily-leetcode-150-problem-at-9-am-ist", tmp_path)
    assert result is not None
    assert result.name == "skill_deliver_daily_leetcode.md"


def test_returns_none_when_no_overlap(tmp_path: Path):
    _write_skill(tmp_path, "skill_unrelated_topic")
    result = _find_matching_skill("apply-for-jobs", tmp_path)
    assert result is None


def test_picks_best_overlap_when_multiple_candidates(tmp_path: Path):
    _write_skill(tmp_path, "skill_leetcode")              # 1 overlap (leetcode)
    _write_skill(tmp_path, "skill_deliver_daily_leetcode")  # 2 overlaps (daily, leetcode)
    result = _find_matching_skill("daily-leetcode-150-problem-at-9-am-ist", tmp_path)
    assert result is not None
    assert result.name == "skill_deliver_daily_leetcode.md"


# ── update_skill_on_failure ──


def test_no_op_when_reason_empty(tmp_path: Path):
    _write_skill(tmp_path, "skill_x", "# x\n")
    result = update_skill_on_failure("brief-morning", "", memory_dir=tmp_path)
    assert result is None
    # File body unchanged
    assert (tmp_path / "skill_x.md").read_text() == "# x\n"


def test_no_op_when_no_matching_skill(tmp_path: Path):
    # No skills at all in the dir
    result = update_skill_on_failure("any-task", "anything", memory_dir=tmp_path)
    assert result is None


def test_appends_watch_out_section_when_none_exists(tmp_path: Path):
    _write_skill(
        tmp_path,
        "skill_deliver_daily_leetcode",
        "# Skill: Deliver Daily Leetcode\n\nSome steps.\n",
    )
    path = update_skill_on_failure(
        "daily-leetcode-150-problem-at-9-am-ist",
        "first failure reason",
        memory_dir=tmp_path,
    )
    assert path is not None
    body = (tmp_path / "skill_deliver_daily_leetcode.md").read_text()
    assert "## Watch outs" in body
    assert "first failure reason" in body
    assert "Watch out (" in body
    # Today's date is in the note
    assert datetime.now().strftime("%Y-%m-%d") in body


def test_appends_into_existing_watch_outs_section(tmp_path: Path):
    body = (
        "# Skill\n\nDo X.\n\n"
        "## Watch outs\n\n"
        "* Watch out (2026-06-01): old reason\n"
    )
    _write_skill(tmp_path, "skill_brief_morning", body)
    update_skill_on_failure("brief-morning", "new reason", memory_dir=tmp_path)
    final = (tmp_path / "skill_brief_morning.md").read_text()
    assert "old reason" in final
    assert "new reason" in final
    # Only ONE "## Watch outs" section in the file
    assert final.count("## Watch outs") == 1


def test_duplicate_reasons_are_ignored(tmp_path: Path):
    _write_skill(tmp_path, "skill_brief_morning", "# x\n")
    update_skill_on_failure("brief-morning", "the same reason", memory_dir=tmp_path)
    first = (tmp_path / "skill_brief_morning.md").read_text()
    update_skill_on_failure("brief-morning", "the same reason", memory_dir=tmp_path)
    second = (tmp_path / "skill_brief_morning.md").read_text()
    # Idempotent — second call MUST NOT add a duplicate
    assert first == second
    assert first.count("the same reason") == 1


def test_old_watch_outs_pruned_at_cap(tmp_path: Path):
    """When MAX_WATCH_OUTS_PER_SKILL is reached, oldest gets dropped."""
    cap = MAX_WATCH_OUTS_PER_SKILL
    body = "# x\n\n## Watch outs\n\n" + "\n".join(
        f"* Watch out (2026-06-{i:02d}): reason {i}" for i in range(1, cap + 1)
    ) + "\n"
    _write_skill(tmp_path, "skill_brief_morning", body)
    update_skill_on_failure("brief-morning", "newest reason", memory_dir=tmp_path)
    final = (tmp_path / "skill_brief_morning.md").read_text()
    # Oldest reason ("reason 1") should be gone
    assert "reason 1" not in final
    # Newest reason is present
    assert "newest reason" in final
    # Total watch-out count stayed at cap
    watch_lines = [
        l for l in final.splitlines()
        if l.lstrip().lower().startswith("* watch out")
    ]
    assert len(watch_lines) == cap


def test_long_reason_is_truncated(tmp_path: Path):
    _write_skill(tmp_path, "skill_brief_morning", "# x\n")
    huge = "X" * 5000
    update_skill_on_failure("brief-morning", huge, memory_dir=tmp_path)
    body = (tmp_path / "skill_brief_morning.md").read_text()
    # Find the bullet "* Watch out (date): ..." — not the "## Watch outs" header
    watch_line = next(
        l for l in body.splitlines()
        if l.lstrip().lower().startswith("* watch out")
    )
    assert len(watch_line) < 320  # 240 cap + date/prefix envelope
    assert watch_line.endswith("…")
