"""Manually trigger a skill refinement run.

PR 3 will auto-detect failing skills from the reflection tick. Until
then, this script is how you launch a refinement on demand — e.g.
after observing a skill failing repeatedly in production logs.

Usage:
    uv run python scripts/refine_skill.py \\
        --skill skill_deliver_daily_leetcode \\
        --context "3 consecutive 403s on leetcode.com via web_fetch"

The script:
  1. Loads the agent's Memory dir from HOMUNCULUS_MEMORY_DIR (default ./memory).
  2. Constructs the failure-context paragraph from --context.
  3. Calls skill_refiner.refine_skill(...) which runs the agent in
     refinement mode (high reasoning, longer turn budget, full tools).
  4. Prints the outcome: saved (new version + rationale), abandoned
     (reason), or exhausted (loop hit max_turns without a decision).

The agent reads/writes the canonical skill_<slug>.md via the
versioned Skills registry. The prior body is archived; rollback via
the registry's revert_to() if the new version turns out to be worse.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skill", required=True,
        help="Skill slug (must start with 'skill_').",
    )
    parser.add_argument(
        "--context", required=True,
        help="One-paragraph: what's been failing and why we think this skill is wrong.",
    )
    parser.add_argument(
        "--max-turns", type=int, default=30,
        help="Iteration budget for the refinement agent. Default 30.",
    )
    parser.add_argument(
        "--model", default=None,
        help="Override HOMUNCULUS_MODEL for this run only.",
    )
    args = parser.parse_args()

    load_dotenv(Path(__file__).parent.parent / ".env")
    if not os.environ.get("HOMUNCULUS_API_KEY"):
        sys.stderr.write("ERROR: HOMUNCULUS_API_KEY is not set\n")
        return 1

    from memory import Memory
    from skill_refiner import refine_skill
    import tools

    memory_dir = Path(os.environ.get("HOMUNCULUS_MEMORY_DIR", "./memory"))
    memory = Memory(memory_dir)
    tools.init(memory, autonomous=True)

    print(f"[refine] starting refinement of {args.skill!r}", flush=True)
    print(f"[refine]   memory dir: {memory_dir.resolve()}", flush=True)
    print(f"[refine]   max turns:  {args.max_turns}", flush=True)
    print(f"[refine]   model:      {args.model or os.environ.get('HOMUNCULUS_MODEL', 'default')}", flush=True)
    print(f"[refine]   context:    {args.context}", flush=True)
    print(flush=True)

    result = refine_skill(
        skill_name=args.skill,
        failure_context=args.context,
        memory=memory,
        model=args.model,
        max_turns=args.max_turns,
    )

    print(flush=True)
    print(f"[refine] outcome: {result.outcome}", flush=True)
    if result.outcome == "saved":
        print(f"[refine]   new version: v{result.new_version}", flush=True)
        print(f"[refine]   rationale:   {result.rationale}", flush=True)
        print(
            f"[refine] roll back with: "
            f"python -c \"from memory import Memory; from skills import Skills; "
            f"Skills(Memory(Path('{memory_dir}')).root).revert_to('{args.skill}', "
            f"{result.new_version - 1 if result.new_version and result.new_version > 1 else 1})\"",
            flush=True,
        )
        return 0
    if result.outcome == "abandoned":
        print(f"[refine]   reason: {result.reason}", flush=True)
        return 2
    # exhausted
    print(f"[refine]   final reply: {result.reason[:300]}", flush=True)
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
