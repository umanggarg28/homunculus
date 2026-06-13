#!/usr/bin/env python3
"""Bootstrap the weekly GitHub profile-health check.

Run once after deploying the github_profile tool:
  $ docker compose exec heartbeat uv run python /app/scripts/bootstrap_github_health.py

Idempotent: writes the skill only if absent, creates the "github-health"
task only if absent.

Scheduled Monday 09:00 user-local — deliberately a DIFFERENT day from
the Sunday weekly digest so no single morning is crowded (Umang's
"consolidated but split across the week").
"""

from __future__ import annotations

import os
import sys
from datetime import timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.environ.setdefault("HOMUNCULUS_USER_TZ_FILE", str(REPO / "workspace" / "user_tz.txt"))

# Whose profile to watch. Umang's public GitHub handle.
GH_USER = os.environ.get("HOMUNCULUS_GITHUB_USER", "umanggarg28")

SKILL_BODY = f"""---
name: skill_github_health
description: Weekly GitHub profile-health check — report week-over-week change in stars, followers, and activity for {GH_USER}.
type: skill
states:
  - tool: github_profile
  - tool: notify
  - tool: complete_task
related: [[skill_weekly_nudge]]
---

# GitHub Health — execution playbook

## Goal
ONE short Monday-morning Telegram message on the state of the
recruiter-visible GitHub surface. The harness diffs week-over-week for
you — you just read the diff and report what actually moved.

## Steps

1. **Call `github_profile()` with NO arguments.** It uses the operator's
   configured handle — never type a username, never guess one from a
   name. It returns a diff against last week PLUS the current snapshot.
   Possible results:
   - `FIRST SNAPSHOT` — first run, no baseline. Report the current
     totals (stars, followers, public repos) as a starting point.
   - `NO CHANGE` — nothing moved. Send a one-line "quiet week on GitHub"
     note (still clears the 40-char minimum) OR skip the flourish and
     just confirm the totals.
   - `CHANGED` — read the `+`/`-` lines. `+` on a repo's star/fork count
     is the news ("homunculus +3 stars"). Ignore churn that isn't
     meaningful.
2. **Compose the message** — lead with the change, then the headline
   totals. Keep it to a few lines.
3. **Call `notify(text=...)`** — open with `📈 GitHub —` so it's
   recognizable.
4. **Call `complete_task(task_id="github-health", result="...")`.**

## Message shape

```
📈 GitHub — <Date>

This week: <what changed, or "quiet — no new stars/followers">
Totals: <S> stars · <F> followers · <R> public repos
```

## Success criteria (mirror the task)
- `notify_called`
- `notify_min_chars` ≥ 40
- `notify_contains` "GitHub"

## Watch outs
- If github_profile returns an ERROR saying the handle is unknown, do
  NOT guess a username. In a chat, ask the operator for their GitHub
  handle and save it with update_world_state(github_user='<handle>');
  on a scheduled run with no one to ask, record_failure noting the
  handle is unconfigured.
- If github_profile returns BLOCKED (rate limit) or another ERROR, do
  NOT invent numbers — record_failure and stop. The API allows 60/hr
  unauthenticated; a single retry is fine, looping is not.
- Don't celebrate noise. 0→0 stars is not "growth"; say it's quiet.
- One message. This is the Monday counterpart to the Sunday digest,
  not a second digest.
"""


def _next_monday_9am_user_naive() -> str:
    """Next Monday 09:00 as naive user-local wall clock (TaskStore's
    frame). Uses now_user_naive to avoid the macOS 'IST' ZoneInfo trap."""
    from user_tz import now_user_naive
    now_local = now_user_naive()
    # Monday = 0. Days until next Monday; if today is Monday before 9am,
    # fire today, else next Monday.
    days_ahead = (0 - now_local.weekday()) % 7
    target = (now_local + timedelta(days=days_ahead)).replace(
        hour=9, minute=0, second=0, microsecond=0
    )
    if target <= now_local:
        target += timedelta(days=7)
    return target.isoformat(timespec="seconds")


def _ensure_skill_file() -> None:
    memory_dir = Path(os.environ.get("HOMUNCULUS_MEMORY_DIR", str(REPO / "workspace" / "memory")))
    memory_dir.mkdir(parents=True, exist_ok=True)
    target = memory_dir / "skill_github_health.md"
    if target.exists() and target.read_text(encoding="utf-8") == SKILL_BODY:
        print(f"[bootstrap_github_health] skill file already current: {target}")
        return
    action = "updated" if target.exists() else "wrote"
    target.write_text(SKILL_BODY, encoding="utf-8")
    print(f"[bootstrap_github_health] {action} skill file: {target}")


def main() -> int:
    from tasks import TaskStore

    _ensure_skill_file()

    store = TaskStore(Path(os.environ.get("HOMUNCULUS_TASKS_DIR", str(REPO / "workspace" / "tasks"))))
    existing = next((t for t in store.all() if t["id"] == "github-health"), None)
    if existing is not None:
        print("[bootstrap_github_health] task already exists — leaving alone.")
        print(f"  due_at: {existing.get('due_at')}  status: {existing.get('status')}")
        return 0

    due_at = _next_monday_9am_user_naive()
    task = store.create(
        title="GitHub health",
        description=(
            f"Weekly GitHub profile-health check for {GH_USER} — see "
            "skill_github_health.md. Call github_profile, read the "
            "week-over-week diff, send one short Monday update via notify."
        ),
        due_at=due_at,
        recurrence="weekly",
        notify=True,
        success_criteria=[
            {"type": "notify_called"},
            {"type": "notify_min_chars", "n": 40},
            {"type": "notify_contains", "text": "GitHub"},
        ],
        skill="skill_github_health",
    )
    print(f"[bootstrap_github_health] created task '{task['id']}'")
    print(f"  due_at:     {task['due_at']} (next Mon 09:00 user-local)")
    print(f"  recurrence: {task['recurrence']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
