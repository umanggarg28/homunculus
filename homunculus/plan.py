"""Current execution plan — a visible step-by-step checklist the agent builds
for multi-step tasks, then checks off as it works.

Pattern from `agents/1_foundations/5_extra.ipynb` (and Claude Code's own task
list): decompose the task into todos, carry out each, mark it done. Making the
plan an explicit, rendered artifact does two things — it forces plan-before-act
(the structural answer to ambiguous flailing) and it makes the agent's
multi-step reasoning *visible* in the chat UI.

One active plan per workspace (single-user model), persisted to
`${HOMUNCULUS_TASKS_DIR}/plan.json` so every process and the web feed see the
same list. Concurrency-safe via the shared file lock.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from homunculus.locking import file_lock


def _plan_path() -> Path:
    base = Path(os.environ.get("HOMUNCULUS_TASKS_DIR", "./tasks"))
    return base / "plan.json"


def _read() -> list[dict]:
    p = _plan_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def _write(steps: list[dict]) -> None:
    p = _plan_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(p.with_suffix(".json.lock")):
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(steps, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)


def set_plan(steps: list[str]) -> list[dict]:
    """Replace the current plan with a fresh list of pending steps."""
    plan = [{"text": str(s).strip(), "done": False, "note": ""} for s in steps if str(s).strip()]
    _write(plan)
    return plan


def complete(index: int, note: str = "") -> list[dict] | None:
    """Mark the 1-based step done. Returns None if the index is out of range."""
    plan = _read()
    if not (1 <= index <= len(plan)):
        return None
    plan[index - 1]["done"] = True
    plan[index - 1]["note"] = note.strip()
    _write(plan)
    return plan


def current() -> list[dict]:
    return _read()


def render(plan: list[dict]) -> str:
    """Markdown checklist — renders with strike-through/checkboxes in the UI."""
    if not plan:
        return "(no active plan)"
    lines = []
    for i, step in enumerate(plan, 1):
        box = "x" if step.get("done") else " "
        suffix = f" — {step['note']}" if step.get("note") else ""
        lines.append(f"- [{box}] {i}. {step.get('text', '')}{suffix}")
    return "\n".join(lines)
