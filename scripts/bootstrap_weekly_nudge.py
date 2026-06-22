#!/usr/bin/env python3
"""Bootstrap T3.10 — the Weekly Nudge recurring task.

Run once after the skill_weekly_nudge.md memory is in place:
  $ docker compose exec heartbeat uv run python /app/scripts/bootstrap_weekly_nudge.py

Idempotent: if a "weekly-nudge" task already exists, it's left alone.

Schedules for the next Sunday at 09:00 in the user's timezone — late
enough that the daily brief (07:00) has already gone out, so the user
sees a calm sequence on Sunday morning rather than two notifications
in the same minute.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, UTC

try:
    from zoneinfo import ZoneInfo
except ImportError:
    print("zoneinfo not available — Python 3.9+ required.", file=sys.stderr)
    sys.exit(2)


def _user_tz_name() -> str:
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from homunculus.user_tz import get_user_tz_name  # type: ignore[import-not-found]
        return get_user_tz_name()
    except Exception:
        return "Asia/Kolkata"


def _next_sunday_9am_in_user_tz() -> str:
    tz = ZoneInfo(_user_tz_name())
    now_local = datetime.now(tz)
    # Python: Monday=0 .. Sunday=6. Days until next Sunday: (6 - weekday) % 7.
    days_ahead = (6 - now_local.weekday()) % 7
    target = (now_local + timedelta(days=days_ahead)).replace(
        hour=9, minute=0, second=0, microsecond=0
    )
    if target <= now_local:
        target = target + timedelta(days=7)
    target_utc = target.astimezone(UTC).replace(tzinfo=None)
    return target_utc.isoformat(timespec="seconds")


_SKILL_BODY = """---
name: skill_weekly_nudge
description: How to compose and deliver the weekly nudge — one Telegram message every Sunday morning surfacing things that fall through the cracks across a week.
type: skill
related: [[skill_daily_brief]]
---

# Weekly Nudge — execution playbook

## Goal
ONE concise Telegram message every Sunday morning. The daily brief
handles *today*; this one handles *the week*. Surface things that are
slow-moving enough to escape the daily view but worth a glance once a
week: idle tasks, decaying skills, recurring failure patterns,
memories the user might want to revisit.

Tone: a calm pointer, not an alarm. If there's nothing to surface,
the message is allowed to be tiny.

## What counts as nudge-worthy

Use these heuristics — not all need to fire, just whatever's true:

1. **Idle active tasks** — `read_file("tasks/tasks.json")`. Any task
   with `status == "active"`, `last_runs` empty OR latest run > 7
   days ago, and `due_at` more than 24h away. The user created it,
   never came back to it.

2. **Skills with repeated failures** — `list_files("memory/")` for
   `skill_*.md` files, then `read_file()` each. A skill where
   `failure_count` ≥ 3 OR `consecutive_failures` > 0 deserves
   attention; mention the skill name and the recent failure reason
   if recorded.

3. **Decaying skills** — skills where `last_used_at` is older than
   30 days but `call_count` was previously > 5. The user might have
   stopped using a workflow without realising.

4. **Failure clusters** — if 3+ tasks failed in the past 7 days
   with similar `last_runs[*].failure_reason`, that's a pattern
   worth flagging together rather than as separate task alerts.

5. **Stale archived memory** — if `archival_memory_search` returns
   nothing recently used (last 30 days) for a topic the user asked
   about in chat this week, mention "I have notes on X from
   <date> — want me to surface them?".

## Steps

1. Survey state via the tools above. Don't read everything — sample
   what's likely to be informative.
2. Compose the message in the shape below.
3. Call `notify(text=<message>)` as the ACTUAL tool call (not in
   assistant_reply prose).
4. Call `complete_task(task_id="weekly-nudge", result="Weekly nudge:
   <N> items surfaced.")`.

## Message shape

```
📡 Weekly check-in — <Date in IST>

Idle (<count>):
• <task title> — created <N>d ago, no runs

Decay (<count>):
• <skill name> — last used <N>d ago, was used <M> times

Patterns:
• <N> tasks failed with "<reason snippet>" this week

Quiet wins:
• <task title> — <N> successful runs this week

(Optional one-line closing thought.)
```

If everything is quiet, send the minimal version:

```
📡 Weekly check-in — <Date>. Quiet week. No idle tasks, no decay, no patterns.
```

## Success criteria (mirror the task's success_criteria contract)

- `notify_called` — exactly one notify()
- `notify_min_chars` ≥ 60 — even the quiet version clears this
- `notify_contains` "Weekly check-in" — sanity check this is the
  weekly nudge and not some other notification

## Format rules

- IST times.
- Bullet character: `•`.
- Header emoji: `📡` — the only visual flair.
- Keep total length under ~600 chars; this is a glance, not a report.
- NEVER include a "you should do X" list. Surface, don't prescribe —
  the user decides what to act on.

## Watch outs

- Don't nudge about a task on the day it was created — `created_at`
  must be at least 7 days ago to count as "idle".
- Don't double up with the daily brief. If something is already a
  "today" alert, skip it here — the brief covered it.
- If state files fail to load, send: "📡 Weekly check-in. I couldn't
  read system state — please check." Better degraded than silent.
- Suppress the "Quiet wins" section if the count is 0; don't render
  empty headers.
"""


def _ensure_skill_file() -> None:
    from pathlib import Path
    memory_dir = Path(os.environ.get("HOMUNCULUS_MEMORY_DIR", "./memory"))
    memory_dir.mkdir(parents=True, exist_ok=True)
    target = memory_dir / "skill_weekly_nudge.md"
    if target.exists():
        print(f"[bootstrap_weekly_nudge] skill file exists — leaving alone: {target}")
        return
    target.write_text(_SKILL_BODY, encoding="utf-8")
    print(f"[bootstrap_weekly_nudge] wrote skill file: {target}")


def main() -> int:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from homunculus.tasks import TaskStore  # type: ignore[import-not-found]
    from pathlib import Path

    _ensure_skill_file()

    store = TaskStore(Path(os.environ.get("HOMUNCULUS_TASKS_DIR", "./tasks")))
    existing = next((t for t in store.all() if t["id"] == "weekly-nudge"), None)
    if existing is not None:
        print("[bootstrap_weekly_nudge] task already exists — leaving alone.")
        print(f"  due_at: {existing.get('due_at')}")
        print(f"  status: {existing.get('status')}")
        return 0

    due_at = _next_sunday_9am_in_user_tz()
    task = store.create(
        title="Weekly nudge",
        description=(
            "Compose and deliver the weekly nudge — see "
            "skill_weekly_nudge.md for the playbook. ONE Telegram "
            "message every Sunday morning surfacing idle tasks, "
            "decaying skills, and recurring failure patterns."
        ),
        due_at=due_at,
        recurrence="weekly",
        notify=True,
        success_criteria=[
            {"type": "notify_called"},
            {"type": "notify_min_chars", "n": 60},
            {"type": "notify_contains", "text": "Weekly check-in"},
        ],
    )
    print(f"[bootstrap_weekly_nudge] created task '{task['id']}'")
    print(f"  title:      {task['title']}")
    print(f"  due_at:     {task['due_at']} (next Sun 09:00 in {_user_tz_name()})")
    print(f"  recurrence: {task['recurrence']}")
    print(f"  criteria:   {json.dumps(task.get('success_criteria'))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
