"""MCP tools the refinement-mode agent uses to commit its decision.

These are intentionally tiny: validate input shape, hand off to the
`skills.Skills` registry (for save) or just record (for abandon).

The runner (`skill_refiner.refine_skill`) wires the registry instance
onto `tools._state._skills` before calling the agent. These tools read
it back. If `_skills` is None — meaning these tools were called outside
a refinement run — they refuse with a clear error rather than silently
saving into the wrong place.
"""

from __future__ import annotations

from skill_refiner import RefinementResult

from . import _state


def save_refined_skill(skill_name: str, new_body: str, rationale: str) -> str:
    """Commit the refined skill body to the registry.

    Returns the human-readable outcome (also recorded for the runner to
    pick up). Refuses with an ERROR if the body is empty, the skill name
    is invalid, or the call happens outside a refinement run.
    """
    skills = getattr(_state, "_skills", None)
    if skills is None:
        return (
            "ERROR: save_refined_skill is only valid inside a "
            "skill-refinement run. The Skills registry is not wired."
        )
    if not skill_name or not isinstance(skill_name, str):
        return "ERROR: skill_name is required"
    body = (new_body or "").strip()
    if len(body) < 50:
        return (
            f"ERROR: new_body is too short ({len(body)} chars). A skill "
            f"that compresses to one line is probably a stub — write the "
            f"full procedure."
        )

    try:
        version = skills.save(
            skill_name,
            body,
            source="refinement-tick",
            rationale=(rationale or "").strip()[:500],
        )
    except ValueError as e:
        return f"ERROR: {e}"

    # Record on tool state so the runner can read the outcome out
    # without re-loading the registry.
    _state._refinement_outcome = RefinementResult(  # type: ignore[attr-defined]
        skill_name=skill_name,
        outcome="saved",
        new_version=version,
        rationale=(rationale or "").strip()[:500],
    )
    return (
        f"Saved {skill_name} v{version}. The next execution tick will "
        f"use this revised body. Prior version archived under "
        f".skill_history/{skill_name}/."
    )


def abandon_refinement(reason: str) -> str:
    """Explicit failure marker. The original skill stays canonical."""
    skill_name = "unknown"
    skills = getattr(_state, "_skills", None)
    if skills is not None:
        # No clean way to know WHICH skill we were refining without
        # threading it through; the runner sets _refinement_target.
        skill_name = getattr(_state, "_refinement_target", "unknown")

    reason = (reason or "").strip()[:500]
    _state._refinement_outcome = RefinementResult(  # type: ignore[attr-defined]
        skill_name=skill_name,
        outcome="abandoned",
        reason=reason,
    )
    return (
        f"Refinement abandoned. Reason: {reason}. The original skill "
        f"body is unchanged. The next refinement run can try again."
    )
