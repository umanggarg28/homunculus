"""skills.load_skill_playbook — parse YAML frontmatter, return states + body.

The heartbeat tick uses this to drive the agent through a fixed tool
sequence (state_sequence on _run_loop, PR #132) instead of relying on
the model to recall its own playbook. Failure modes (missing file,
malformed YAML, missing 'tool' on a state entry) must degrade
gracefully to legacy free-form rather than crash the tick.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from skills import load_skill_playbook


def _write(tmp: Path, name: str, content: str) -> None:
    (tmp / f"{name}.md").write_text(content, encoding="utf-8")


# ---- happy path -------------------------------------------------------


def test_loads_states_and_body(tmp_path):
    _write(tmp_path, "skill_x", (
        "---\n"
        "name: skill_x\n"
        "type: skill\n"
        "states:\n"
        "  - tool: read_file\n"
        "    args:\n"
        "      path: memory/foo.md\n"
        "  - tool: notify\n"
        "  - tool: complete_task\n"
        "---\n\n"
        "# Body\n"
        "Steps go here.\n"
    ))
    states, body = load_skill_playbook(tmp_path, "skill_x")
    assert states == [
        {"tool": "read_file", "args": {"path": "memory/foo.md"}},
        {"tool": "notify"},
        {"tool": "complete_task"},
    ]
    assert "# Body" in body and "Steps go here" in body


def test_body_excludes_frontmatter(tmp_path):
    _write(tmp_path, "skill_x", (
        "---\nname: skill_x\nstates:\n  - tool: notify\n---\n\n"
        "BODY-CONTENT\n"
    ))
    _, body = load_skill_playbook(tmp_path, "skill_x")
    assert "name: skill_x" not in body
    assert "BODY-CONTENT" in body


# ---- skills without states (legacy) ----------------------------------


def test_no_states_key_returns_none(tmp_path):
    """Legacy skills (no states: declaration) keep working — heartbeat
    falls back to free-form for them."""
    _write(tmp_path, "skill_legacy", (
        "---\nname: skill_legacy\ntype: skill\n---\n\n"
        "Free-form playbook with no state machine.\n"
    ))
    states, body = load_skill_playbook(tmp_path, "skill_legacy")
    assert states is None
    assert "Free-form" in body


def test_no_frontmatter_at_all_returns_none(tmp_path):
    """A skill with no frontmatter — whole file is body, no states."""
    _write(tmp_path, "skill_bare", "Just a body, no frontmatter.\n")
    states, body = load_skill_playbook(tmp_path, "skill_bare")
    assert states is None
    assert body == "Just a body, no frontmatter.\n"


# ---- malformed / hostile inputs --------------------------------------


def test_malformed_yaml_degrades_to_legacy(tmp_path):
    """Broken YAML in frontmatter must not crash the loader (and by
    extension, the heartbeat tick). Fall back to no-states."""
    _write(tmp_path, "skill_bad", (
        "---\n"
        "name: skill_bad\n"
        "states: [unclosed list\n"
        "---\n\n"
        "body\n"
    ))
    states, _ = load_skill_playbook(tmp_path, "skill_bad")
    assert states is None


def test_states_not_a_list_degrades(tmp_path):
    _write(tmp_path, "skill_x", (
        "---\nname: skill_x\nstates: not-a-list\n---\n\nbody\n"
    ))
    states, _ = load_skill_playbook(tmp_path, "skill_x")
    assert states is None


def test_state_missing_tool_field_degrades(tmp_path):
    """A state without `tool:` is malformed. Fall back rather than
    pass garbage to _run_loop which would raise mid-tick."""
    _write(tmp_path, "skill_x", (
        "---\n"
        "name: skill_x\n"
        "states:\n"
        "  - args: {x: 1}\n"  # no tool
        "---\n\nbody\n"
    ))
    states, _ = load_skill_playbook(tmp_path, "skill_x")
    assert states is None


def test_unclosed_frontmatter_fence_returns_no_states(tmp_path):
    """Opening `---\n` with no closing fence — degrade to legacy."""
    _write(tmp_path, "skill_x", (
        "---\nname: skill_x\n"
        "states:\n  - tool: notify\n"
        "(no closing fence)\n"
    ))
    states, body = load_skill_playbook(tmp_path, "skill_x")
    assert states is None
    # body falls back to the whole file
    assert "name: skill_x" in body


def test_missing_file_raises_FileNotFoundError(tmp_path):
    """A task pointing at a nonexistent skill should surface clearly
    so the operator can fix the wiring (vs. silently doing nothing)."""
    with pytest.raises(FileNotFoundError):
        load_skill_playbook(tmp_path, "skill_does_not_exist")
