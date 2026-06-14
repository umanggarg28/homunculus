"""Deterministic validation gate for agent-authored skills.

This is the safety interlock that lets a weak free model rewrite its own
behavior without being able to break itself. Every skill the agent
proposes — a brand-new one from a chat request, or an edit to a failing
one — passes through validate_skill_body BEFORE it can be committed.
A proposal that fails validation never reaches the approval queue.

The checks are deliberately structural and cheap (no LLM, zero
inference cost — SkillOpt's point): a skill must be a well-formed
playbook the heartbeat can actually execute, not prose that looks
plausible. The model owns the *content*; the harness guarantees the
*shape*.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import yaml

# Skills are addressed as skill_<slug> across the registry, the task
# `skill=` field, and the heartbeat loader. Enforce it so a proposal
# can't shadow a non-skill memory or land somewhere the loader can't find.
_SKILL_NAME_RE = re.compile(r"^skill_[a-z0-9][a-z0-9_]{1,62}$")

# A real playbook has steps. Anything shorter is a stub that compresses
# to "do the thing" — matches save_refined_skill's existing floor.
_MIN_BODY_CHARS = 80


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    frontmatter: dict = field(default_factory=dict)
    states_tools: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "errors": self.errors,
            "warnings": self.warnings,
            "frontmatter": self.frontmatter,
            "states_tools": self.states_tools,
        }


def _split_frontmatter(text: str) -> tuple[dict | None, str, str | None]:
    """Return (frontmatter_dict, body, error). frontmatter_dict is None
    on any structural problem, with error explaining why."""
    if not text.startswith("---\n"):
        return None, text, "skill must open with a '---' YAML frontmatter block"
    end = text.find("\n---\n", 4)
    if end == -1:
        return None, text, "frontmatter block is not closed with a '---' line"
    fm_text = text[4:end]
    body = text[end + 5:]
    try:
        parsed = yaml.safe_load(fm_text)
    except yaml.YAMLError as e:
        return None, body, f"frontmatter is not valid YAML: {e}"
    if not isinstance(parsed, dict):
        return None, body, "frontmatter did not parse to a mapping of fields"
    return parsed, body, None


def validate_skill_body(
    body: str,
    *,
    expected_name: str | None = None,
    known_tools: set[str] | None = None,
) -> ValidationResult:
    """Structurally validate a proposed skill markdown document.

    expected_name — if given (an edit to a known skill), the frontmatter
      `name` must match it; a proposal can't silently retarget another
      skill.
    known_tools — if given, every tool referenced in a `states:` block
      must exist in it; otherwise the heartbeat would force a call to a
      tool that isn't registered. Omitted in pure-logic tests.
    """
    errors: list[str] = []
    warnings: list[str] = []

    fm, body_text, fm_err = _split_frontmatter(body or "")
    if fm_err is not None:
        return ValidationResult(ok=False, errors=[fm_err])

    name = fm.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append("frontmatter must include a 'name'")
    elif not _SKILL_NAME_RE.match(name):
        errors.append(
            f"name {name!r} must be a skill_<slug> identifier "
            "(lowercase letters/digits/underscores, starting 'skill_')"
        )
    elif expected_name is not None and name != expected_name:
        errors.append(
            f"name {name!r} does not match the skill being edited "
            f"({expected_name!r}) — a proposal may not retarget another skill"
        )

    if not isinstance(fm.get("description"), str) or not fm["description"].strip():
        errors.append("frontmatter must include a non-empty 'description'")

    if fm.get("type") != "skill":
        errors.append("frontmatter 'type' must be 'skill'")

    if len(body_text.strip()) < _MIN_BODY_CHARS:
        errors.append(
            f"playbook body is too short ({len(body_text.strip())} chars) — "
            "write the actual procedure, not a stub"
        )

    states_tools: list[str] = []
    states = fm.get("states")
    if states is not None:
        if not isinstance(states, list) or not all(isinstance(s, dict) for s in states):
            errors.append("'states' must be a list of {tool: <name>} entries")
        else:
            for s in states:
                tool = s.get("tool")
                if not isinstance(tool, str) or not tool:
                    errors.append("each 'states' entry needs a string 'tool'")
                    continue
                states_tools.append(tool)
            if known_tools is not None:
                missing = [t for t in states_tools if t not in known_tools]
                if missing:
                    errors.append(
                        f"'states' references tools that don't exist: {missing}"
                    )

    # A skill may declare its own success_criteria (the quality bar for any
    # task that runs it — see skills.effective_success_criteria). Validate
    # them so a proposal can't ship a criterion the TaskGuard can't evaluate.
    sc = fm.get("success_criteria")
    if sc is not None:
        if not isinstance(sc, list):
            errors.append("frontmatter 'success_criteria' must be a list")
        else:
            for c in normalize_criteria(sc):
                if c.get("type") not in _KNOWN_CRITERIA:
                    errors.append(
                        f"unknown success_criteria type {c.get('type')!r}; "
                        f"allowed: {sorted(_KNOWN_CRITERIA)}"
                    )

    return ValidationResult(
        ok=not errors,
        errors=errors,
        warnings=warnings,
        frontmatter=fm,
        states_tools=states_tools,
    )


# success_criteria types the TaskGuard understands. A new-skill proposal
# that bundles a task must use these — a criterion the guard can't
# evaluate would never gate the task.
_KNOWN_CRITERIA = {
    "notify_called", "notify_min_chars", "notify_has_code",
    "notify_contains", "notify_matches", "notify_unique",
}

# Compact single-key YAML form a skill author naturally writes
# (e.g. `- notify_min_chars: 200`, `- notify_contains: "Foo"`) → the
# canonical {"type": <name>, <param>: <value>} shape TaskGuard reads.
# Maps each value-bearing criterion to its TaskGuard param key.
_COMPACT_VALUE_KEY = {
    "notify_min_chars": "n",
    "notify_contains": "text",
    "notify_matches": "pattern",
    "notify_unique": "pattern",
}


def normalize_criteria(criteria: list) -> list[dict]:
    """Coerce success_criteria to the list-of-dicts shape TaskGuard reads.

    Three input shapes are accepted, so task specs and skill frontmatter
    can both be written the natural way:
    - bare string  "notify_called"          → {"type": "notify_called"}
    - canonical    {"type": "...", ...}      → passed through untouched
    - compact YAML {"notify_min_chars": 200} → {"type": "notify_min_chars",
                                                "n": 200}
    Stored criteria are always {"type": ...} dicts so TaskGuard's
    c.get('type') never hits a string or a key-as-type."""
    out: list[dict] = []
    for c in criteria or []:
        if isinstance(c, str):
            out.append({"type": c})
        elif isinstance(c, dict):
            if "type" in c:
                out.append(c)
            elif len(c) == 1:
                (key, val), = c.items()
                norm = {"type": key}
                if key in _COMPACT_VALUE_KEY:
                    norm[_COMPACT_VALUE_KEY[key]] = val
                out.append(norm)
            else:
                # Malformed (multi-key, no 'type') — pass through so
                # validation surfaces it as an unknown type.
                out.append(c)
    return out


def validate_task_spec(task_spec: dict) -> list[str]:
    """Validate a bundled task spec (chat-authored 'teach it a job').
    Returns a list of errors (empty = ok)."""
    errors: list[str] = []
    if not isinstance(task_spec, dict):
        return ["task spec must be an object"]
    if not str(task_spec.get("title", "")).strip():
        errors.append("task spec needs a 'title'")
    recurrence = task_spec.get("recurrence", "none")
    if recurrence not in ("none", "daily", "weekly"):
        errors.append("task 'recurrence' must be none|daily|weekly")
    if recurrence == "none" and not task_spec.get("due_at"):
        errors.append("a one-shot task needs a 'due_at'")
    criteria = task_spec.get("success_criteria", [])
    if criteria and not isinstance(criteria, list):
        errors.append("'success_criteria' must be a list")
    else:
        for c in normalize_criteria(criteria):
            if c.get("type") not in _KNOWN_CRITERIA:
                errors.append(
                    f"unknown success_criteria type {c.get('type')!r}; allowed: "
                    f"{sorted(_KNOWN_CRITERIA)}"
                )
    return errors
