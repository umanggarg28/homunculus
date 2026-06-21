"""remember() must not overwrite existing skill_*.md files.

Live finding 2026-06-10: the agent has been calling
`remember(type="skill", name="skill_X", body="paraphrased steps...")`
on its own playbooks. Each call rewrites the canonical body with a
summary, progressively destroying the playbook. Traced via
_events.jsonl tool_call entries showing 5+ remember() rewrites of
skill_deliver_daily_leetcode.md over two weeks, each one shorter than
the last, until the GraphQL queries were gone and the body read
'... (rest omitted)'.

Fix: forbid remember() from overwriting existing skill_*.md. Updates
must go through write_file() (explicit) or save_refined_skill() (the
versioned refinement path).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


def _real_memory(tmp_path: Path):
    """Memory has side effects on tools/_state; load a fresh module."""
    sys.modules.pop("homunculus.memory", None)
    import importlib
    memory = importlib.import_module("homunculus.memory")
    return memory.Memory(tmp_path)


# ---- the core guard --------------------------------------------------


def test_remember_rejects_overwriting_existing_skill(tmp_path):
    """The exact failure mode from prod: agent calls remember() with
    type='skill' on a name whose file already exists. Must reject."""
    mem = _real_memory(tmp_path)

    # First write — new skill, allowed.
    mem.remember(
        name="skill_x",
        description="A test skill",
        type="skill",
        body="Step 1: do the canonical thing with full detail.",
    )
    skill_path = tmp_path / "skill_x.md"
    assert skill_path.exists()
    original = skill_path.read_text(encoding="utf-8")
    assert "canonical thing" in original

    # Second write — overwrite attempt with a paraphrase. Must reject.
    result = mem.remember(
        name="skill_x",
        description="Updated skill",
        type="skill",
        body="(paraphrased summary)",
    )
    assert result.startswith("ERROR"), (
        f"second remember() on an existing skill must error, got: {result!r}"
    )
    assert "skill" in result.lower(), result
    # The on-disk file must be untouched.
    assert skill_path.read_text(encoding="utf-8") == original, (
        "rejected remember() must not modify the file"
    )


def test_error_message_points_at_safe_alternatives(tmp_path):
    """The agent needs to know what to do INSTEAD — the error must
    name write_file and save_refined_skill so it can self-correct."""
    mem = _real_memory(tmp_path)
    mem.remember(name="skill_x", description="d", type="skill", body="initial body")
    result = mem.remember(name="skill_x", description="d", type="skill", body="new body")
    assert "write_file" in result, result
    assert "save_refined_skill" in result, result


# ---- legitimate paths still work -------------------------------------


def test_creating_a_new_skill_still_works(tmp_path):
    """The guard only fires on EXISTING skills. New skill creation
    must remain a one-shot remember() call."""
    mem = _real_memory(tmp_path)
    result = mem.remember(
        name="skill_brand_new",
        description="d",
        type="skill",
        body="step 1 ...",
    )
    assert not result.startswith("ERROR"), result
    assert (tmp_path / "skill_brand_new.md").exists()


def test_non_skill_types_can_still_be_overwritten(tmp_path):
    """remember() on type='project' / 'feedback' / 'user' still allows
    updates — those are the agent's working memory and SHOULD be
    overwritable. The guard is specific to skills."""
    mem = _real_memory(tmp_path)
    mem.remember(
        name="delivered_problems",
        description="d",
        type="project",
        body="original entries",
    )
    result = mem.remember(
        name="delivered_problems",
        description="d",
        type="project",
        body="updated entries",
    )
    assert not result.startswith("ERROR"), result
    path = tmp_path / "project_delivered_problems.md"
    assert "updated entries" in path.read_text(encoding="utf-8")


# ---- bypass paths still work (write_file, Skills.save) ---------------


def test_write_file_can_still_modify_a_skill_file(tmp_path):
    """The guard is on remember() specifically. Direct write_file()
    is the EXPLICIT escape hatch and must remain unaffected — the
    user / refinement agent uses it to update a skill intentionally."""
    mem = _real_memory(tmp_path)
    mem.remember(name="skill_x", description="d", type="skill", body="initial")
    path = tmp_path / "skill_x.md"
    # Simulate a write_file call updating the skill.
    path.write_text("---\nname: skill_x\n---\n\nintentionally edited body\n",
                    encoding="utf-8")
    assert "intentionally edited" in path.read_text(encoding="utf-8")
