#!/usr/bin/env python3
"""Upgrade the Sunday weekly nudge into the consolidated weekly digest.

Run after deploying week_in_review:
  $ docker compose exec heartbeat uv run python /app/scripts/bootstrap_weekly_digest.py

What it does:
  - Version-updates skill_weekly_nudge (via Skills.save, prior body
    archived) to a body that opens with a deterministic SYSTEM REPORT
    section sourced from week_in_review() — replacing the old
    instructions to hand-read tasks.json and tally failures, which is
    the exact LLM-counts-things antipattern week_in_review exists to
    kill. The forward-looking nudge content (idle tasks, decaying
    skills) is kept.
  - Leaves the existing Sunday "weekly-nudge" task untouched (same day,
    same time) — this is a skill upgrade, not a new notification.

Idempotent: re-running re-saves the same body as a new version. Safe,
but only run when the body actually changed.
"""

from __future__ import annotations

import os
import sys
from datetime import timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.environ.setdefault("HOMUNCULUS_USER_TZ_FILE", str(REPO / "workspace" / "user_tz.txt"))

CONSOLIDATED_BODY = """---
name: skill_weekly_nudge
description: The weekly digest — one Sunday-morning Telegram message combining a deterministic system self-report with forward-looking nudges (idle tasks, decaying skills).
type: skill
related: [[skill_daily_brief]], [[skill_github_health]]
---

# Weekly Digest — execution playbook

## Goal
ONE Sunday-morning Telegram message. Two halves:
1. **System report** — what the agent did this week and what it cost.
   The agent reports on itself: deliveries, failures, spend vs budget.
2. **Nudges** — slow-moving things worth a glance: idle tasks, decaying
   skills. Surface, don't prescribe.

The daily brief handles *today*; this handles *the week*. Calm tone. If
it's all quiet, the message is allowed to be short.

## Steps

1. **Call `week_in_review()`.** This is the deterministic source of
   truth for the system report — cost vs budget, per-day spend,
   notifications sent, tasks completed, failures recorded, guard
   blocks, task outcomes. Do NOT hand-read tasks.json or tally the
   event log yourself; this tool already did it correctly. Format its
   numbers; never recompute them.

2. **Survey nudge-worthy state** (the part week_in_review doesn't
   cover):
   - **Idle active tasks** — `read_file("tasks/tasks.json")`. A task
     with `status == "active"`, `created_at` ≥ 7 days ago, `last_runs`
     empty or latest run > 7 days ago, and `due_at` > 24h away. Created
     and forgotten.
   - **Decaying / failing skills** — `list_files("memory/")` for
     `skill_*.md`; a skill with `failure_count` ≥ 3 or
     `consecutive_failures` > 0, or last used > 30 days ago after heavy
     prior use, is worth a mention.

3. **Compose** in the shape below. Lead with the system report.
4. **`notify(text=...)`** — ONE message. Open with `🗓 Weekly check-in`.
5. **`complete_task(task_id="weekly-nudge", result="Weekly digest sent.")`.**

## Message shape

```
🗓 Weekly check-in — <Date in IST>

This week:
• <N> deliveries · <M> failures · ¢<spend> of ¢<budget> budget
• <one-line highlight or "all systems nominal">

Nudges (<count>):
• <idle task> — created <N>d ago, no runs
• <decaying skill> — <reason>

(Optional one-line closing thought.)
```

If everything is quiet:

```
🗓 Weekly check-in — <Date>. <N> deliveries, no failures, ¢<spend> spent. Quiet week — nothing idle or decaying.
```

## Success criteria (mirror the task)
- `notify_called` — exactly one notify()
- `notify_min_chars` ≥ 60
- `notify_contains` "Weekly check-in"

## Format rules
- IST times. Bullet `•`. Header emoji `🗓` only.
- Costs in cents, from week_in_review (already budget-aware).
- Under ~700 chars. A glance, not a ledger dump.
- Surface, don't prescribe — the user decides what to act on.

## Watch outs
- Don't double up with the daily brief or the Monday GitHub check —
  if something was already today's alert, skip it here.
- Don't nudge a task created < 7 days ago.
- If week_in_review or state files fail to load, send a degraded
  message saying so. Better degraded than silent.
- Suppress empty sections; never render a header with no items.
"""


def _next_sunday_9am_user_naive() -> str:
    """Next Sunday 09:00 as naive user-local wall clock (TaskStore's
    frame). now_user_naive avoids the macOS 'IST' ZoneInfo trap."""
    from user_tz import now_user_naive
    now_local = now_user_naive()
    # Monday=0 .. Sunday=6. Days until next Sunday.
    days_ahead = (6 - now_local.weekday()) % 7
    target = (now_local + timedelta(days=days_ahead)).replace(
        hour=9, minute=0, second=0, microsecond=0
    )
    if target <= now_local:
        target += timedelta(days=7)
    return target.isoformat(timespec="seconds")


def _ensure_task() -> None:
    from tasks import TaskStore

    store = TaskStore(Path(os.environ.get("HOMUNCULUS_TASKS_DIR", str(REPO / "workspace" / "tasks"))))
    existing = next((t for t in store.all() if t["id"] == "weekly-nudge"), None)
    if existing is not None and existing.get("status") == "active":
        print(f"[bootstrap_weekly_digest] weekly-nudge task active — due {existing.get('due_at')}.")
        return
    due_at = _next_sunday_9am_user_naive()
    task = store.create(
        title="Weekly nudge",
        description=(
            "The weekly digest — see skill_weekly_nudge.md. ONE Sunday "
            "message: a deterministic system self-report (week_in_review) "
            "plus forward-looking nudges (idle tasks, decaying skills)."
        ),
        due_at=due_at,
        recurrence="weekly",
        notify=True,
        success_criteria=[
            {"type": "notify_called"},
            {"type": "notify_min_chars", "n": 60},
            {"type": "notify_contains", "text": "Weekly check-in"},
        ],
        skill="skill_weekly_nudge",
    )
    print(f"[bootstrap_weekly_digest] created task '{task['id']}' — due {task['due_at']} (next Sun 09:00 user-local).")


def main() -> int:
    from skills import Skills

    memory_dir = Path(os.environ.get("HOMUNCULUS_MEMORY_DIR", str(REPO / "workspace" / "memory")))
    skills = Skills(memory_dir)

    current = skills.load("skill_weekly_nudge")
    if current is not None and current.strip() == CONSOLIDATED_BODY.strip():
        print("[bootstrap_weekly_digest] skill already at consolidated body — no change.")
    else:
        version = skills.save(
            "skill_weekly_nudge",
            CONSOLIDATED_BODY,
            source="user-edit",
            rationale="Consolidate the weekly self-report (week_in_review) into the Sunday digest; drop the hand-read-tasks.json failure tally.",
        )
        print(f"[bootstrap_weekly_digest] saved skill_weekly_nudge v{version} (prior archived).")

    _ensure_task()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
