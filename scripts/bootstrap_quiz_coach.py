#!/usr/bin/env python3
"""Bootstrap the spaced-repetition quiz coach.

Run once after deploying the quiz tools:
  $ docker compose exec heartbeat uv run python /app/scripts/bootstrap_quiz_coach.py

Idempotent:
  - sets the learning AREA (the agent grows the curriculum itself — no
    hardcoded topic list)
  - writes skill_quiz_coach.md only if absent
  - creates the evening "quiz-coach" task only if absent

Schedules for today/tomorrow at 20:00 in the user's timezone — after the
work day, well clear of the 07:00 brief and the Sunday digest.
"""

from __future__ import annotations

import os
import sys
from datetime import timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.environ.setdefault("HOMUNCULUS_USER_TZ_FILE", str(REPO / "workspace" / "user_tz.txt"))

# The ONLY human-supplied input: the broad learning domain. The agent
# autonomously chooses sub-topics within it, researches them, and grows
# the curriculum — there is no hardcoded topic list (that would defeat the
# point of an autonomous coach). Override with HOMUNCULUS_QUIZ_AREA.
DEFAULT_AREA = "deep learning"

SKILL_BODY = """---
name: skill_quiz_coach
description: Daily self-directed spaced-repetition coach — autonomously choose and research a sub-topic within the user's learning area, ask one question, grade the reply.
type: skill
related: [[skill_daily_brief]]
---

# Quiz Coach — autonomous execution playbook

## What this is
A SELF-DIRECTED spaced-repetition coach for the user's chosen learning
AREA. The harness owns scheduling (what's due, intervals, rotation); YOU
own choosing which sub-topic to explore, researching it, writing the
question, and grading. There is NO fixed topic list — you grow the
curriculum yourself.

## Phase 1 — Evening tick (the recurring task fires)

1. **Call `quiz_pick()`** with no argument. Branch on `status`:
   - `"picked"` (a due topic to review) → go to step 3 with `topic`.
   - `"explore"` (nothing due) → choose ONE specific sub-topic within the
     returned `area` that is NOT in `recently_covered`, **`web_search` it**
     to ground an accurate, current question, then call
     `quiz_pick(topic="<the sub-topic you chose>")` to register it.
   - `"pending"` → a prior question is still open today; re-ask that SAME
     topic.
   - `"explore"` with an empty `area` → ask the user which broad domain to
     be quizzed on; do NOT guess one.
2. **Compose ONE question** on the topic — probe understanding, not a
   definition prompt. web_search first if accuracy/recency helps.
3. **Call `notify(text=...)`** opening with `🧠 Quiz —` so it's
   recognizable. Tell the user to just reply with their answer.
4. **Call `complete_task(task_id="quiz-coach", result="Asked: <topic>")`.**

Do NOT grade in this phase — the user hasn't answered yet.

## Phase 2 — Grading (when the user replies in chat)

When the user's message clearly answers your `🧠 Quiz` question:

1. Judge it: `correct` (got the key idea), `partial` (right direction,
   missed something), or `wrong` (incorrect / "I don't know").
2. **Call `quiz_grade(outcome=...)`.**
3. Reply with brief, warm feedback (2–4 sentences); mention when it comes
   back up (`quiz_grade` returns `next_review_in_days`).

Be encouraging — durable learning, not a test score.

## Success criteria (mirror the task)
- `notify_called`, `notify_min_chars` ≥ 40, `notify_contains` "Quiz"

## Watch outs
- ONE question per evening; quiz_pick gives one focus — don't batch.
- NEVER pull from a fixed list — choose and research sub-topics yourself.
- Vary the angle; don't repeat a recently-covered sub-topic.
"""


def _next_8pm_user_naive() -> str:
    """Next 20:00 as a NAIVE wall-clock string in the user's timezone —
    the frame TaskStore stores due_at in (now_user_naive). Avoids
    constructing ZoneInfo from a possibly-abbreviated name like macOS's
    'IST' (not a valid IANA key); now_user_naive only trusts a stored
    IANA name and otherwise uses system-local wall clock."""
    from user_tz import now_user_naive
    now_local = now_user_naive()
    target = now_local.replace(hour=20, minute=0, second=0, microsecond=0)
    if target <= now_local:
        target += timedelta(days=1)
    return target.isoformat(timespec="seconds")


def _set_area() -> None:
    from quiz import QuizStore
    path = Path(os.environ.get("HOMUNCULUS_QUIZ_FILE", str(REPO / "workspace" / "quiz.json")))
    area = os.environ.get("HOMUNCULUS_QUIZ_AREA", DEFAULT_AREA)
    QuizStore(path).set_area(area)
    print(f"[bootstrap_quiz_coach] set learning area to {area!r} in {path}")
    print("[bootstrap_quiz_coach] no topics seeded — the agent grows the curriculum itself")


def _ensure_skill_file() -> None:
    memory_dir = Path(os.environ.get("HOMUNCULUS_MEMORY_DIR", str(REPO / "workspace" / "memory")))
    memory_dir.mkdir(parents=True, exist_ok=True)
    target = memory_dir / "skill_quiz_coach.md"
    if target.exists():
        print(f"[bootstrap_quiz_coach] skill file exists — leaving alone: {target}")
        return
    target.write_text(SKILL_BODY, encoding="utf-8")
    print(f"[bootstrap_quiz_coach] wrote skill file: {target}")


def main() -> int:
    from tasks import TaskStore

    _set_area()
    _ensure_skill_file()

    store = TaskStore(Path(os.environ.get("HOMUNCULUS_TASKS_DIR", str(REPO / "workspace" / "tasks"))))
    existing = next((t for t in store.all() if t["id"] == "quiz-coach"), None)
    if existing is not None:
        print("[bootstrap_quiz_coach] task already exists — leaving alone.")
        print(f"  due_at: {existing.get('due_at')}  status: {existing.get('status')}")
        return 0

    due_at = _next_8pm_user_naive()
    task = store.create(
        title="Quiz coach",
        description=(
            "Run the daily spaced-repetition quiz — see skill_quiz_coach.md. "
            "Call quiz_pick, ask ONE question on the returned concept via "
            "notify, then grade the user's reply with quiz_grade."
        ),
        due_at=due_at,
        recurrence="daily",
        notify=True,
        success_criteria=[
            {"type": "notify_called"},
            {"type": "notify_min_chars", "n": 40},
            {"type": "notify_contains", "text": "Quiz"},
        ],
        skill="skill_quiz_coach",
    )
    print(f"[bootstrap_quiz_coach] created task '{task['id']}'")
    print(f"  due_at:     {task['due_at']} (next 20:00 user-local)")
    print(f"  recurrence: {task['recurrence']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
