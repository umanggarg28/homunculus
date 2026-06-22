"""Skill refinement runner — autonomous skill redesign.

When a skill keeps failing for non-execution reasons (a site adds anti-
bot, an API moves, a contract changes), the model is following the right
procedure on the wrong assumptions. Patching the symptom in the harness
doesn't help — the next stale skill breaks differently.

The structural fix (this module) runs the model in a *different mode*:
no success_criteria, no per-tick budget pressure, high reasoning, full
tool access, and a single goal — produce a working replacement procedure
or explain why none exists.

Lineage
-------
- Hermes 5-stage loop: `refine_skill` is an explicit stage with permission
  to rewrite the procedure based on observed outcomes.
- Letta sleep-time agent: a separate process reviews task histories and
  proposes memory edits offline.
- OpenClaw skill registry: skills carry `last_known_good_at`; on N
  consecutive failures the skill is flagged for refinement.

Operating mode
--------------
The refinement Agent runs with:
  - source="refinement"            → tool_choice="auto" (exploratory)
  - reasoning effort: high          (the model needs to actually think)
  - max_turns: configurable (default 30, vs 20 for normal ticks)
  - separate system prompt          (no skill execution, no success_criteria)
  - full tool catalogue             (web_search, web_fetch, python,
                                     read_file, plus the new
                                     save_refined_skill / abandon_refinement)

End states
----------
The refinement run ends when the agent calls exactly one of:
  - save_refined_skill(skill_name, new_body, rationale) → success
  - abandon_refinement(reason) → explicit failure
Or the loop exhausts max_turns → implicit abandonment.

Either way the original skill stays canonical unless save_refined_skill
fires. Failures of the refinement run never corrupt the registry.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid import at module-load time
    from homunculus.memory import Memory


# Default model for refinement runs.
#
# Heartbeat / chat use openai/gpt-oss-120b for cost — but the live test
# of skill-refinement against the LeetCode skill showed gpt-oss at
# medium reasoning cycles through 404'ing mirror URLs for 30 turns
# instead of trying the GraphQL endpoint we explicitly named in the
# context. That's a model-capability limit for the rare design-mode
# task ("discover a working API given a goal"), not an architecture
# bug.
#
# Haiku 4.5 scores Intelligence Index 31 (vs gpt-oss-120b's 33 but with
# substantially better real-world tool-use reliability per agentic
# benchmarks) and costs ~$0.05 per 30-turn refinement run at typical
# prompt sizes. Refinement runs are rare — maybe 1 per skill per
# month when something breaks — so the cost delta vs gpt-oss-120b
# is well inside the $5/month budget.
#
# Override via HOMUNCULUS_MODEL_REFINEMENT in .env or --model on the
# CLI for one-off experiments.
DEFAULT_REFINEMENT_MODEL = "anthropic/claude-haiku-4-5"


REFINEMENT_PROMPT_TEMPLATE = """You are in SKILL-REFINEMENT mode.

A recurring task's procedure ("skill") has been failing for non-execution
reasons. Your job is NOT to deliver the task right now — it's to figure
out a procedure that will work reliably going forward, and save it.

## Current skill

Name: `{skill_name}`

```markdown
{current_body}
```

## Why we think it's broken

{failure_context}

## What to do

Explore freely. You have full tool access — web_search, web_fetch, python,
read_file, etc. Use them. Iterate. Try APIs. Read documentation. Test
your candidate approach end-to-end if you can.

When you have a candidate replacement procedure that you've verified
works, call exactly one of:

  save_refined_skill(
      skill_name="{skill_name}",
      new_body="<the new markdown body of the skill>",
      rationale="<one-paragraph: what was broken, what you changed, what you verified>"
  )

Or, if after thorough exploration no working approach exists:

  abandon_refinement(
      reason="<one-paragraph: what you tried and why none of it works>"
  )

## Constraints on the new skill body

- Must be markdown.
- Must end the task with one of: complete_task, continue_task,
  cancel_task, record_failure.
- Prefer the FEWEST tool calls possible (5 or fewer ideal).
- Prefer official APIs over scraping. If the data source has a
  documented endpoint, use it.
- If you reference specific URLs/params/queries, you must have
  verified them in this session.
- Include one short example of the expected tool sequence so the
  next execution agent has a concrete pattern to follow.

## What NOT to do

- Don't try to execute the original task (don't call notify, complete_task,
  etc. as if you were the daily agent).
- Don't propose a procedure you haven't actually verified.
- Don't write a generic essay — write a skill the next execution agent
  will follow.

You have up to {max_turns} iterations. Begin.
"""


@dataclass(frozen=True)
class RefinementResult:
    """What came out of a refinement run."""
    skill_name: str
    outcome: str  # "saved" | "abandoned" | "exhausted"
    new_version: int | None = None
    rationale: str = ""
    reason: str = ""  # populated when outcome == "abandoned"


def build_refinement_prompt(
    skill_name: str,
    current_body: str,
    failure_context: str,
    max_turns: int,
) -> str:
    """Render the refinement system prompt.

    Pure function so tests can pin the shape without running the agent.
    """
    return REFINEMENT_PROMPT_TEMPLATE.format(
        skill_name=skill_name,
        current_body=current_body.rstrip() or "(no existing body — this is a brand-new skill)",
        failure_context=failure_context.strip(),
        max_turns=max_turns,
    )


def _resolve_refinement_model(explicit: str | None) -> str:
    """Pick the model for a refinement run.

    Priority: explicit CLI/arg → HOMUNCULUS_MODEL_REFINEMENT env →
    DEFAULT_REFINEMENT_MODEL (Haiku 4.5).
    """
    if explicit:
        return explicit
    env = os.environ.get("HOMUNCULUS_MODEL_REFINEMENT")
    if env:
        return env
    return DEFAULT_REFINEMENT_MODEL


def refine_skill(
    skill_name: str,
    failure_context: str,
    memory: Memory,
    model: str | None = None,
    max_turns: int = 30,
) -> RefinementResult:
    """Run the refinement Agent on `skill_name`.

    Caller responsibilities:
      - Decide WHEN to refine (PR 3 will do this from the reflection
        tick; for now it's manual via scripts/refine_skill.py).
      - Provide a useful `failure_context` (which failure pattern, recent
        run results, what's been tried). The prompt is only as good as
        this context.

    Model selection: refinement uses a stronger model than execution by
    default (Haiku 4.5 vs gpt-oss-120b) — see DEFAULT_REFINEMENT_MODEL.
    Override via HOMUNCULUS_MODEL_REFINEMENT env or --model on the CLI.

    Side effects on success:
      - skills.Skills.save(skill_name, new_body, source="refinement-tick",
        rationale=...) fires via the save_refined_skill MCP tool.
      - The canonical skill file is replaced; the prior version is
        archived. Rollback is registry.revert_to(skill_name, N).

    On abandonment or exhaustion the registry is untouched.

    This module deliberately does not import the Agent at module load —
    Agent has heavy provider/event dependencies; lazy import keeps
    `skill_refiner` cheap to import in tests and in the reflection-tick
    path (which only needs `build_refinement_prompt` to plan).
    """
    from homunculus.core import Agent, get_config
    from homunculus.skills import Skills
    from homunculus.tools import _state as tool_state

    skills = Skills(memory.root)
    current_body = skills.load(skill_name) or ""
    resolved_model = _resolve_refinement_model(model)

    system_prompt = build_refinement_prompt(
        skill_name=skill_name,
        current_body=current_body,
        failure_context=failure_context,
        max_turns=max_turns,
    )

    # The refinement Agent gets a longer iteration budget than the
    # default heartbeat tick. Snapshot, override, restore — the
    # alternative (a per-call parameter) would touch every callsite.
    cfg = get_config()
    original_max = cfg.loop.max_turns
    # Pydantic v2 dataclasses are immutable; mutate via field replacement
    # in a tightly scoped block. Tests verify cleanup happens.
    try:
        object.__setattr__(cfg.loop, "max_turns", max_turns)
        # Ensure the tools layer can see the same Skills instance the
        # save_refined_skill MCP tool will write into. tool_state.init()
        # is the canonical setter but it also flips autonomous/mode; we
        # only need to bridge `_skills`. Direct attribute set keeps the
        # other tool state intact.
        tool_state._skills = skills  # type: ignore[attr-defined]

        agent = Agent(memory=memory, system_prompt=system_prompt, model=resolved_model)
        # Single kick: the system prompt IS the task. The user-message
        # slot is just a "begin" — needed because _run_loop is structured
        # around (system + user) → (tool calls)+ pairs.
        reply = agent.chat(
            "Begin the refinement. Explore, verify, then save_refined_skill "
            "or abandon_refinement.",
            source="refinement",
        )
    finally:
        object.__setattr__(cfg.loop, "max_turns", original_max)
        # Best-effort: read out the outcome the agent recorded via tools.
        tool_state._skills = None  # type: ignore[attr-defined]

    # The save / abandon tool implementations record the outcome on the
    # tool_state singleton; we read it out here. If neither fired, the
    # loop exhausted its budget without committing — treat as abandoned.
    outcome = getattr(tool_state, "_refinement_outcome", None)
    if outcome is None:
        return RefinementResult(
            skill_name=skill_name,
            outcome="exhausted",
            reason=reply[:500],
        )
    # Clear for next run.
    tool_state._refinement_outcome = None  # type: ignore[attr-defined]
    return outcome
