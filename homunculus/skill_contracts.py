"""Deterministic contract checks for the live skill registry.

Proposal validation protects new edits before approval. Contract checks protect
the registry as a whole: every existing skill file should still match its
filename, parse as a skill, and reference only tools the runtime exposes.
No model calls, no network, just structural assertions suitable for pytest/CI.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from homunculus.skill_validation import validate_skill_body


@dataclass(frozen=True)
class SkillContractIssue:
    skill: str
    message: str


def validate_registry_contracts(
    memory_root: Path,
    *,
    known_tools: set[str] | None = None,
) -> list[SkillContractIssue]:
    issues: list[SkillContractIssue] = []
    if not memory_root.exists():
        return issues
    for path in sorted(memory_root.glob("skill_*.md")):
        expected = path.stem
        try:
            body = path.read_text(encoding="utf-8")
        except OSError as e:
            issues.append(SkillContractIssue(expected, f"could not read: {e}"))
            continue
        result = validate_skill_body(body, expected_name=expected, known_tools=known_tools)
        for error in result.errors:
            issues.append(SkillContractIssue(expected, error))
    return issues


def assert_registry_contracts(memory_root: Path, *, known_tools: set[str] | None = None) -> None:
    issues = validate_registry_contracts(memory_root, known_tools=known_tools)
    if not issues:
        return
    rendered = "\n".join(f"- {i.skill}: {i.message}" for i in issues)
    raise AssertionError(f"Skill contract failures:\n{rendered}")
