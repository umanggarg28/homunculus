#!/usr/bin/env python3
"""Bootstrap T1.3 — the Morning Brief recurring task.

Run once after the skill_daily_brief.md memory is in place:
  $ docker compose exec heartbeat uv run python /app/scripts/bootstrap_daily_brief.py

Idempotent: if a "morning-brief" task already exists, it's left alone.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:
    print("zoneinfo not available — Python 3.9+ required.", file=sys.stderr)
    sys.exit(2)


# Default fire time. Will be next 7 AM in the user's TZ — read TZ from
# user_tz.py (the same source heartbeat uses) so the bootstrap respects
# wherever the browser said the user is.
def _user_tz_name() -> str:
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from homunculus.user_tz import get_user_tz_name  # type: ignore[import-not-found]
        return get_user_tz_name()
    except Exception:
        return "Asia/Kolkata"


def _next_7am_in_user_tz() -> str:
    tz = ZoneInfo(_user_tz_name())
    now_local = datetime.now(tz)
    target = now_local.replace(hour=7, minute=0, second=0, microsecond=0)
    if target <= now_local:
        target = target + timedelta(days=1)
    # Convert to naive UTC — the format the task store expects.
    target_utc = target.astimezone(timezone.utc).replace(tzinfo=None)
    return target_utc.isoformat(timespec="seconds")


# Skill memory body — copied to workspace/memory/skill_daily_brief.md if
# the file doesn't already exist there. Kept here (rather than in
# workspace/memory/ which is gitignored) so the bootstrap is self-contained:
# every install can run this script and get both the skill and the task.
_SKILL_BODY = """---
name: skill_daily_brief
description: How to compose and deliver Umang's morning brief — one Telegram message per day at 7 AM IST summarising what matters for the day.
type: skill
related: []
---

# Daily Brief — execution playbook

## Goal
ONE concise Telegram message in the morning. Calm by default, informative when there's actually something to know. The user should be able to read it in ≤ 30 seconds and walk away knowing today's commitments and any system anomalies.

## Steps

1. **Call `task_health_summary()`** — this is the ONLY source of truth for what to put in the brief. It returns JSON with `today_commitments`, `alerts`, and `recently_recovered`. Do NOT re-read `tasks.json` and second-guess. Reading the raw `last_runs` array has consistently led to misreporting historic failures as current alerts.
2. **`today_commitments`** is your commitments list. Use it as-is.
3. **`alerts`** is your alerts list. Use it as-is. An empty `alerts` array means **no alerts** — say so by omitting the Alerts section entirely. Never invent alerts from `recently_recovered` (those have already healed).
4. **Read recent reflection memory** (if it exists) — `read_file("memory/_brief_carryover.md")`. This is where yesterday's brief leaves notes for today (e.g. "user said they'll deal with X today — check in").
5. **Compose the message** in the shape below.
6. **Call `notify(text=<message>)`** as the ACTUAL tool call (not in your assistant_reply prose — see skill_deliver_daily_leetcode for the contract).
7. **Call `complete_task(task_id="morning-brief", result="Delivered brief: <N> commitments, <M> alerts.")`.**

## Message shape

```
☀️ Morning, Umang. <Date in IST>

Today (<count>):
• <task title> — <when>
• <task title> — <when>

Alerts (<count>):
⚠️ <task title> — failed <N> times since yesterday
⚠️ <skill name> hasn't been refined in 30+ days but its task is failing

Carryover from yesterday: <one line, if present>
```

If there are **zero** commitments AND zero alerts AND no carryover, send the minimal version:

```
☀️ Morning, Umang. <Date>. Nothing on your plate. Day is yours.
```

## Success criteria (mirror the task's success_criteria contract)

- `notify_called` — exactly one notify(), not multiple
- `notify_min_chars` ≥ 80 — even the "nothing on your plate" version clears this
- `notify_contains` "Morning, Umang" — sanity check this is the morning brief and not some other notification

## Format rules

- IST times, not UTC.
- Bullet character: `•` (single-byte, looks crisp in Telegram).
- Header emoji: `☀️` — the only visual flair. Resist adding emoji to body content.
- Do NOT include code blocks here — the LeetCode skill needs code blocks, this one specifically should NOT (criteria explicitly omit `notify_has_code`).
- Keep lines under ~80 chars so the message wraps cleanly on phone.

## Carryover file

If today's brief decides something needs follow-up tomorrow ("user has a deadline Friday — remind them Wednesday"), write a single line to `memory/_brief_carryover.md` using `write_file`. The file is overwritten daily — never accumulate.

## Watch outs

- "Today" means the IST calendar day, not 24h-from-now. A task due at 11 PM IST tonight is "today"; a task due at 7 AM IST tomorrow is NOT "today" — it'll be in tomorrow's brief.
- If task data fails to load, send a degraded brief: "☀️ Morning. I couldn't read tasks.json — system may be down. Please check." Better to alert than to fall silent.
- Do not summarize multiple notifications into one — the brief is the only message. Other tasks fire their own notify() calls separately.
"""


def _ensure_skill_file() -> None:
    """Write skill_daily_brief.md if it doesn't already exist."""
    from pathlib import Path
    memory_dir = Path(os.environ.get("HOMUNCULUS_MEMORY_DIR", "./memory"))
    memory_dir.mkdir(parents=True, exist_ok=True)
    target = memory_dir / "skill_daily_brief.md"
    if target.exists():
        print(f"[bootstrap_daily_brief] skill file exists — leaving alone: {target}")
        return
    target.write_text(_SKILL_BODY, encoding="utf-8")
    print(f"[bootstrap_daily_brief] wrote skill file: {target}")


def main() -> int:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from homunculus.tasks import TaskStore  # type: ignore[import-not-found]
    from pathlib import Path

    _ensure_skill_file()

    store = TaskStore(Path(os.environ.get("HOMUNCULUS_TASKS_DIR", "./tasks")))
    existing = next((t for t in store.all() if t["id"] == "morning-brief"), None)
    if existing is not None:
        print(f"[bootstrap_daily_brief] task already exists — leaving alone.")
        print(f"  due_at: {existing.get('due_at')}")
        print(f"  status: {existing.get('status')}")
        return 0

    due_at = _next_7am_in_user_tz()
    task = store.create(
        title="Morning brief",
        description=(
            "Compose and deliver Umang's daily morning brief — see "
            "skill_daily_brief.md for the playbook. ONE Telegram message "
            "with today's commitments and any system alerts."
        ),
        due_at=due_at,
        recurrence="daily",
        notify=True,
        success_criteria=[
            {"type": "notify_called"},
            {"type": "notify_min_chars", "n": 80},
            {"type": "notify_contains", "text": "Morning, Umang"},
        ],
    )
    print(f"[bootstrap_daily_brief] created task '{task['id']}'")
    print(f"  title:    {task['title']}")
    print(f"  due_at:   {task['due_at']} (next 7 AM in {_user_tz_name()})")
    print(f"  recurrence: {task['recurrence']}")
    print(f"  criteria: {json.dumps(task.get('success_criteria'))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
