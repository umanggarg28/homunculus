"""
Homunculus heartbeat — autonomous self-prompting loop.

Wakes every HEARTBEAT_INTERVAL_MINUTES minutes, runs ONE fresh agent
session with the heartbeat prompt, then sleeps. The agent can read its
memory, write workspace files, and remember new facts on its own.
shell_exec is disabled (see tools.py — autonomous=True).

This is what makes Homunculus "autonomous" instead of just "a chatbot":
it acts even when no human is talking to it.

Run:
    docker compose up -d heartbeat        # background, auto-restart
    docker compose logs -f heartbeat      # watch what it's doing
    docker compose stop heartbeat         # stop it
"""

import os
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

import events
import tools
from core import Agent
from memory import Memory
from tasks import TaskStore


HEARTBEAT_PROMPT_TEMPLATE = """It's a scheduled heartbeat tick — no user is
talking to you right now. The current time is {now_iso}.

Python already checked structured task state and found these due tasks:

{due_tasks}

Handle ONLY the due task(s). Do not invent unrelated proactive work.

Examples of useful proactive actions:
- For notification tasks, use `notify()` with a concise message.
- For research/delivery tasks, do the minimal needed work and save or
  notify the result as the task description asks.
- When a task is handled, call `complete_task(task_id, result)` so
  one-shot tasks stop firing and recurring tasks advance.

Scheduling: by default the next tick is in ~10 minutes. If you'd like
to adjust that (e.g. wake at 8am tomorrow before a deadline, or in
2 hours to check progress), call `schedule_next_tick("YYYY-MM-DDTHH:MM:SS")`.
Must be in the future, within 24h.

Important rules:
- DO NOT read the daily log files unless you have a specific recall
  task. Logs contain your own previous heartbeat output and reading
  them every tick creates a feedback loop. Trust your memory index.
- If a due task cannot be completed, leave it active and briefly say why.
- shell_exec is disabled. If a task would need shell access, call
  remember() to leave a note for the user.
"""


REFLECTION_PROMPT_TEMPLATE = """It's a daily REFLECTION tick — no user is
talking to you right now. The current date is {today}.

Your task: review what happened yesterday ({yesterday}) and learn from it.

Step 1 — Read yesterday's log file:
    read_file("memory/logs/{yesterday_path}.md")
(If the file doesn't exist, yesterday was quiet — say so in one line and stop.)

Step 2 — Look for PATTERNS worth carrying forward:
- Did the user correct you on something? → save as a "feedback" memory.
- Did the user reveal an ongoing goal, deadline, or project state? →
  save as a "project" memory.
- Did you make a mistake (wrong tool, wrong assumption) that a future
  you should avoid? → save as a "feedback" memory phrased as a rule.
- Did the user confirm a non-obvious choice worked well? → save as a
  "feedback" memory so future-you keeps doing it.
- Did you successfully complete a NON-TRIVIAL multi-step workflow
  (e.g. "deliver daily LeetCode" = read tracker → pick problem →
  fetch solution → notify → update tracker → complete task)? → save
  as a "skill" memory with name `skill_<slug>` listing the steps in
  order. This is how you learn your own job: next time the same
  trigger fires, read the skill and replay. A skill memory's body
  should start with "Trigger:" (when to use it) and then numbered
  steps. Keep it short — the procedure, not the prose.

Step 3 — Save AT MOST 3 new memories via remember(). Fewer is fine.
Skip anything trivial or already covered by an existing memory in your
index. Quality over quantity. If a new fact updates an existing memory,
call remember() with the SAME `name` as that entry to overwrite in
place — don't create duplicates.

Step 4 — Memory hygiene pass. Scan your memory index for:
- Duplicates (two entries that describe the same fact) — call forget()
  on the older / less-accurate one.
- Contradictions (a newer memory contradicts an older one) — call
  forget() on the outdated one.
- Plain garbage that's no longer relevant (a project that's over,
  a deadline that's passed) — call forget().
Call forget() AT MOST 2 times this tick. Be conservative — when in
doubt, leave it. Losing context is worse than carrying old facts.

Step 5 — Reply with a ONE-LINE summary of what you learned (or
"nothing notable from yesterday"). Then stop.

Important:
- Reading yesterday's log is the ONLY log read you should do this tick.
  Don't chain into older logs.
- Don't write to workspace files. This tick is for memory only.
- Don't call notify(). Reflections are silent.
- shell_exec is disabled.
"""


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _yesterday_iso_and_path() -> tuple[str, str]:
    """Return (YYYY-MM-DD, YYYY/MM/YYYY-MM-DD) for yesterday."""
    y = datetime.now() - timedelta(days=1)
    iso = y.strftime("%Y-%m-%d")
    path_form = y.strftime("%Y/%m/%Y-%m-%d")
    return iso, path_form


def tick(memory: Memory, model: str | None) -> None:
    """One heartbeat iteration — fresh agent, one prompt, then discard.

    If reflection is due (we haven't reflected today yet) AND there's a
    yesterday to look at, this tick runs the reflection prompt instead
    of the normal proactive prompt. The marker is updated on success so
    we don't reflect twice in the same calendar day.
    """
    today = _today_str()
    last = memory.get_last_reflection_date()
    do_reflection = last is None or last < today

    now_iso = datetime.now().isoformat(timespec="seconds")

    if do_reflection:
        agent = Agent(memory=memory, model=model)
        yesterday_iso, yesterday_path = _yesterday_iso_and_path()
        print(f"\n[heartbeat] REFLECTION tick at {now_iso} "
              f"(reviewing {yesterday_iso}, model={agent.model})", flush=True)
        prompt = REFLECTION_PROMPT_TEMPLATE.format(
            today=today,
            yesterday=yesterday_iso,
            yesterday_path=yesterday_path,
        )
        response = agent.chat(prompt)
        memory.set_last_reflection_date(today)
        print(f"[agent] {response}", flush=True)
        return

    tasks = TaskStore(Path(os.environ.get("HOMUNCULUS_TASKS_DIR", "./tasks")))
    due_tasks = tasks.due()
    if not due_tasks:
        print(f"\n[heartbeat] tick at {now_iso}: no due tasks; skipping LLM", flush=True)
        return

    agent = Agent(memory=memory, model=model)
    print(
        f"\n[heartbeat] tick at {now_iso}: {len(due_tasks)} due task(s) "
        f"(model={agent.model})",
        flush=True,
    )

    # Stamp `last_fired_at` BEFORE running the agent. This is the dedupe
    # token: if heartbeat crashes mid-tick, the next tick sees a recent
    # fire stamp and suppresses the same task. Without this, the same
    # task would re-fire on every restart until completion.
    for task in due_tasks:
        try:
            tasks.mark_fired(task["id"])
        except Exception as e:
            print(f"[heartbeat] mark_fired failed for {task['id']}: {e}", flush=True)

    prompt = HEARTBEAT_PROMPT_TEMPLATE.format(
        now_iso=now_iso,
        due_tasks=_format_due_tasks(due_tasks),
    )
    started = datetime.now()
    try:
        response = agent.chat(prompt)
    except Exception as e:
        # Record per-task failure so reliability is auditable. Status
        # stays active until CONSECUTIVE_FAILURE_LIMIT (set in tasks.py)
        # which auto-cancels the task; emit a task_failure event for
        # each so the /traces UI can show what went wrong.
        duration = (datetime.now() - started).total_seconds()
        err = f"{type(e).__name__}: {e}"
        for task in due_tasks:
            try:
                updated = tasks.record_failure(task["id"], err, duration_s=duration)
                events.emit(
                    "task_failure",
                    name=task["id"],
                    text=events.truncate_preview(err),
                    result=(
                        f"consecutive_failures={updated.get('consecutive_failures', '?')} "
                        f"status={updated.get('status', '?')}"
                    ),
                )
            except Exception as inner:
                print(f"[heartbeat] record_failure failed for {task['id']}: {inner}", flush=True)
        raise
    print(f"[agent] {response}", flush=True)


def _format_due_tasks(tasks: list[dict]) -> str:
    lines = []
    for task in tasks:
        lines.append(
            f"- id: {task.get('id')}\n"
            f"  title: {task.get('title')}\n"
            f"  due_at: {task.get('due_at')}\n"
            f"  recurrence: {task.get('recurrence', 'none')}\n"
            f"  notify: {task.get('notify', False)}\n"
            f"  description: {task.get('description', '')}"
        )
    return "\n".join(lines)


def main() -> None:
    load_dotenv(Path(__file__).parent / ".env")
    if not os.environ.get("HOMUNCULUS_API_KEY"):
        sys.exit("HOMUNCULUS_API_KEY is not set.")

    interval_min = int(os.environ.get("HEARTBEAT_INTERVAL_MINUTES", "60"))
    memory_dir = Path(os.environ.get("HOMUNCULUS_MEMORY_DIR", "./memory"))
    # Heartbeat's task is simpler than the bot/REPL — pick a smaller
    # default. Saves ~6x on tokens-per-tick. Override via env if needed.
    model = os.environ.get("HOMUNCULUS_MODEL_HEARTBEAT", "openai/gpt-oss-20b")

    memory = Memory(memory_dir)
    tools.init(memory, autonomous=True)

    print(f"[heartbeat] starting, interval = {interval_min} min, model = {model}", flush=True)

    default_interval = interval_min * 60
    while True:
        try:
            tick(memory, model=model)
        except Exception:
            # Don't let one bad tick kill the daemon. Log and continue.
            print("[heartbeat] error during tick:", flush=True)
            traceback.print_exc()

        sleep_seconds = _compute_sleep(memory, default_interval)
        wake_at = (datetime.now() + timedelta(seconds=sleep_seconds)).isoformat(timespec="seconds")
        print(f"[heartbeat] sleeping {sleep_seconds:.0f}s, next tick ~{wake_at}", flush=True)
        time.sleep(sleep_seconds)


def _compute_sleep(memory: Memory, default_seconds: float) -> float:
    """Read the agent's self-scheduled wake time, or fall back to default.

    Pops the scheduled time so each tick decides independently — if the
    agent forgets to schedule next time, we don't reuse a stale value.
    """
    task_store = TaskStore(Path(os.environ.get("HOMUNCULUS_TASKS_DIR", "./tasks")))
    next_task = task_store.next_due_seconds()
    scheduled = memory.pop_next_tick()
    if scheduled is None:
        return min(default_seconds, next_task) if next_task is not None else default_seconds
    try:
        target = datetime.fromisoformat(scheduled)
    except ValueError:
        print(f"[heartbeat] could not parse scheduled time '{scheduled}', using default", flush=True)
        return min(default_seconds, next_task) if next_task is not None else default_seconds
    # Defense-in-depth: schedule_next_tick now persists naive local time,
    # but older stored values might be timezone-aware. Normalize before
    # comparing to datetime.now() (which is naive).
    if target.tzinfo is not None:
        target = target.astimezone().replace(tzinfo=None)
    delta = (target - datetime.now()).total_seconds()
    if delta <= 0:
        print(f"[heartbeat] scheduled time {scheduled} is in the past, using default", flush=True)
        return min(default_seconds, next_task) if next_task is not None else default_seconds
    # The schedule_next_tick tool already caps at 24h on the way in, but
    # double-check here as a defense-in-depth.
    capped = min(delta, 24 * 3600)
    if capped < delta:
        print(f"[heartbeat] capping {delta:.0f}s schedule to 24h", flush=True)
    return min(capped, next_task) if next_task is not None else capped


if __name__ == "__main__":
    main()
