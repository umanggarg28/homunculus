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

from proposals import KIND_NEW_SKILL, KIND_SKILL_EDIT, _store
from skill_validation import normalize_criteria, validate_skill_body, validate_task_spec


def _skills():
    from skills import Skills
    return Skills(Path(os.environ.get("HOMUNCULUS_MEMORY_DIR", "./memory")))


def propose_skill(
    name: str,
    body: str,
    rationale: str = "",
    kind: str | None = None,
    task: dict | None = None,
) -> str:
    """File a skill proposal for human review. Returns JSON describing
    the queued proposal, or the validation errors to fix."""
    name = (name or "").strip()
    if not name:
        return json.dumps({"ok": False, "errors": ["name is required"]})

    skills = _skills()
    exists = skills.load(name) is not None

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
