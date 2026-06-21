"""propose_skill — the agent's interface for authoring its own skills.

The two halves of the self-improvement capability funnel through here:
  #1 refine a failing skill  → propose_skill(kind="skill_edit", ...)
  #2 learn a job from chat   → propose_skill(kind="new_skill", task=...)

It never writes to the live registry. It validates the proposed body
structurally (cheap, no LLM), and on success files a PENDING proposal
that a human approves or rejects in the containment UI. Validation
failures are returned to the agent verbatim so it can fix and re-propose
— the model gets a tight feedback loop, the registry stays protected.

The authoritative tools-exist check and the actual Skills.save happen at
APPROVAL time (transports/web_api), where the full tool catalogue and
the registry are in hand.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from homunculus.proposals import KIND_NEW_SKILL, KIND_SKILL_EDIT, _store
from homunculus.skill_validation import normalize_criteria, validate_skill_body, validate_task_spec


def _skills():
    from homunculus.skills import Skills
    return Skills(Path(os.environ.get("HOMUNCULUS_MEMORY_DIR", "./memory")))


def _edit_field(edit: dict, *names: str) -> str | None:
    """Pull a field from an edit dict, tolerating the aliases a weak model
    reaches for (old / old_str / old_string). Returns None if absent."""
    for n in names:
        if n in edit and edit[n] is not None:
            return str(edit[n])
    return None


def _apply_edits(current: str, edits: list[dict]) -> tuple[str | None, list[str]]:
    """Apply surgical {old, new} string replacements to a skill body, the way
    Letta's core_memory_replace / Anthropic's str_replace editor do: each `old`
    must match EXACTLY and UNIQUELY, or we fail back to the agent so it can add
    surrounding context and retry. Untouched text is never regenerated, so this
    can't drop sections or corrupt unicode — the failure modes of a full rewrite
    that a weak (open-weight) model is especially prone to.

    Returns (new_text, []) on success or (None, [errors])."""
    errors: list[str] = []
    if not isinstance(edits, list) or not edits:
        return None, ["edits must be a non-empty list of {old, new} objects"]

    text = current
    for i, edit in enumerate(edits):
        if not isinstance(edit, dict):
            errors.append(f"edit #{i + 1} must be an object with 'old' and 'new'")
            continue
        old = _edit_field(edit, "old", "old_str", "old_string", "old_content")
        new = _edit_field(edit, "new", "new_str", "new_string", "new_content")
        if old is None or not old:
            errors.append(f"edit #{i + 1}: 'old' is required and must be non-empty")
            continue
        if new is None:
            new = ""  # empty new = deletion (Letta semantics)
        count = text.count(old)
        if count == 0:
            errors.append(
                f"edit #{i + 1}: 'old' text not found in the current skill. "
                f"Read the skill again and copy the exact text verbatim."
            )
        elif count > 1:
            errors.append(
                f"edit #{i + 1}: 'old' text matches {count} places — add surrounding "
                f"context so it identifies exactly one location."
            )
        else:
            text = text.replace(old, new, 1)
    if errors:
        return None, errors
    return text, []


def propose_skill(
    name: str,
    body: str = "",
    rationale: str = "",
    kind: str | None = None,
    task: dict | None = None,
    edits: list[dict] | None = None,
) -> str:
    """File a skill proposal for human review. Returns JSON describing
    the queued proposal, or the validation errors to fix.

    A skill_edit may be expressed either as a full `body` rewrite OR as a list
    of surgical `edits` ({old, new} string replacements against the current
    skill). Prefer `edits` for targeted changes — it's the str_replace pattern
    open-weight models handle most reliably, and it cannot drop or corrupt the
    parts of the skill you didn't touch."""
    name = (name or "").strip()
    if not name:
        return json.dumps({"ok": False, "errors": ["name is required"]})

    skills = _skills()
    current = skills.load(name)
    exists = current is not None

    # Infer kind if the agent didn't state it, then sanity-check against
    # reality so a "new" proposal can't silently clobber an existing skill.
    if kind is None:
        kind = KIND_SKILL_EDIT if exists else KIND_NEW_SKILL
    if kind == KIND_SKILL_EDIT and not exists:
        return json.dumps({
            "ok": False,
            "errors": [f"no skill named {name!r} to edit — use kind='new_skill' to create it"],
        })
    if kind == KIND_NEW_SKILL and exists:
        return json.dumps({
            "ok": False,
            "errors": [f"skill {name!r} already exists — use kind='skill_edit' to change it"],
        })

    # Surgical-edit mode: apply {old, new} replacements to the current skill to
    # produce the full body, which then flows through the same validation and
    # approval path as a hand-written body. This keeps untouched text verbatim.
    if edits is not None:
        if kind == KIND_NEW_SKILL:
            return json.dumps({
                "ok": False,
                "errors": ["edits apply to an existing skill; a new_skill needs a full body"],
            })
        if body:
            return json.dumps({
                "ok": False,
                "errors": ["provide either a full body OR edits, not both"],
            })
        body, edit_errors = _apply_edits(current, edits)
        if edit_errors:
            return json.dumps({"ok": False, "errors": edit_errors}, indent=2)
    elif not body:
        return json.dumps({
            "ok": False,
            "errors": ["provide a full body, or edits=[{old, new}] for a skill_edit"],
        })

    result = validate_skill_body(body, expected_name=name)
    errors = list(result.errors)

    if kind == KIND_NEW_SKILL and task is not None:
        errors += validate_task_spec(task)
    elif kind == KIND_SKILL_EDIT and task is not None:
        errors.append("task spec is only allowed on a new_skill proposal")

    if errors:
        return json.dumps({"ok": False, "errors": errors}, indent=2)

    # Store criteria as dicts so the approval path and TaskGuard never
    # see a bare string the model may have written.
    stored_task = None
    if kind == KIND_NEW_SKILL and task is not None:
        stored_task = {**task, "success_criteria": normalize_criteria(task.get("success_criteria", []))}

    proposal = _store().create(
        kind=kind,
        skill_name=name,
        body=body,
        rationale=rationale,
        source="agent",
        task_spec=stored_task,
        validation=result.as_dict(),
    )
    return json.dumps({
        "ok": True,
        "proposal_id": proposal["id"],
        "kind": kind,
        "status": "pending",
        "message": (
            f"Proposal {proposal['id']} filed for {name} ({kind}). It is "
            "PENDING human review — it will NOT take effect until the "
            "operator approves it on the Overview page (the 'Proposed "
            "skill evolution' panel). Tell the user to review it there. "
            "Do not assume the skill changed; continue with current behavior."
        ),
    }, indent=2)


def list_proposals(status: str = "pending") -> str:
    """List skill proposals (default: pending). Read-only."""
    items = _store().list(status)
    return json.dumps([
        {
            "id": p["id"], "kind": p["kind"], "skill_name": p["skill_name"],
            "status": p["status"], "rationale": p["rationale"], "created_at": p["created_at"],
        }
        for p in items
    ], indent=2)
