"""Score recent task runs against their skill's declared contract.

Read-only: prints a scorecard, changes nothing. Run against a live
workspace (in the container or on the host, wherever the volume is
mounted) to see how cleanly each skill's runs are actually going —
not just "did it complete" (tasks.json already tracks that), but "how
many tool-choice violations and guard corrections did it take to get
there." See homunculus/evals.py for the scoring logic.

Usage:
    uv run python scripts/eval_trajectories.py [--workspace PATH]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from homunculus.evals import load_events, load_tasks, score_all  # noqa: E402


def _fmt(value: float | None, suffix: str = "") -> str:
    return f"{value:.2f}{suffix}" if value is not None else "—"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--workspace", default=str(REPO_ROOT / "workspace"),
        help="Path to the workspace directory (default: ./workspace)",
    )
    args = ap.parse_args()
    workspace = Path(args.workspace)

    tasks = load_tasks(workspace / "tasks" / "tasks.json")
    events = load_events(workspace / "_events.jsonl")
    scorecards = score_all(tasks, events, workspace / "memory")

    if not scorecards:
        print("No skill-linked tasks with run history found.")
        return

    header = f"{'skill':32s} {'runs':>5s} {'compliance':>11s} {'avg viol':>9s} {'avg guard':>10s} {'avg ¢':>7s}  trend"
    print(header)
    print("-" * len(header))
    for task_id, card in sorted(scorecards.items()):
        if card.runs == 0:
            continue
        compliance = f"{card.compliance_rate * 100:.0f}%" if card.compliance_rate is not None else "—"
        print(
            f"{task_id:32s} {card.runs:5d} {compliance:>11s} "
            f"{_fmt(card.avg_violations):>9s} {_fmt(card.avg_guard_fires):>10s} "
            f"{_fmt(card.avg_cost_cents):>7s}  {card.trend}"
        )


if __name__ == "__main__":
    main()
