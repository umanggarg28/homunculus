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
import re
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

import events
import tools
from core import Agent
from memory import Memory
from tasks import TaskStore
from tools.notify import enable_buffer, flush_buffer, clear_buffer as clear_notify_buffer


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

SUCCESS CRITERIA (important):
Some tasks list `success_criteria` — machine-checked rules that your output
must satisfy BEFORE `complete_task` is accepted. If your output fails, you
will receive a BLOCKED message explaining why. Read the criteria for each task
upfront, ensure your work meets them, then call `complete_task`.

Scheduling: by default the next tick is in ~60 minutes (or sooner if
another task is due). If you'd like to adjust that (e.g. wake at 8am
tomorrow before a deadline, or in 2 hours to check progress), call
`schedule_next_tick("YYYY-MM-DDTHH:MM:SS")`. Must be in the future, within 24h.

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

Step 2.5 — Skill evaluate+refine (do this BEFORE saving new memories):
Read the task state file:
    read_file("tasks/tasks.json")

For each task in the file that has `last_runs` entries from yesterday
({yesterday}), check if a skill memory exists for that task. To find
a matching skill: look in your memory index for entries named
`skill_<slug>` where the slug resembles the task title (e.g., task
"Daily LeetCode" → skill_daily-leetcode). For each match:

a) If the task's last_runs from yesterday show ONLY successes:
   No action needed — the skill is working.

b) If the task's last_runs from yesterday show ANY failures:
   - Read the current skill memory body.
   - Identify what step failed (look at the error in `result` field).
   - Rewrite the skill with a corrected step or a "watch out" note
     added to the failing step.
   - Call remember() with the SAME name (e.g. `skill_daily-leetcode`)
     to overwrite the existing skill in place — do NOT create a new one.
   - The updated skill should include a "Last fixed: {yesterday}" note
     at the top so you can track when it was last refined.

c) If a task ran yesterday but has NO matching skill yet (task was
   manually run or is new), AND it succeeded, treat it like Step 2 —
   save a new skill memory if the workflow was non-trivial.

This is how skills self-improve: each reflection tick patches the
procedure based on real run data rather than leaving broken steps in
place forever.

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


class TaskGuard:
    """Pi-style output guard for scheduled task delivery.

    Installed as a pre-execute hook on tools.execute() for the duration of a
    heartbeat tick. Intercepts two tool calls:

    - notify()        → records the text so criteria can inspect it.
    - complete_task() → checks the task's success_criteria before allowing
                        the call through. Returns a structured BLOCKED error
                        if any criterion fails, so the agent can correct its
                        output and retry rather than silently recording garbage
                        as a successful delivery.

    Supported criterion types
    ─────────────────────────
    {"type": "notify_called"}
        At least one notify() was called during this tick.

    {"type": "notify_min_chars", "n": N}
        The combined text of all notify() calls is at least N characters.

    {"type": "notify_has_code"}
        At least one notify() text contains a fenced code block (```).

    {"type": "notify_contains", "text": "..."}
        At least one notify() text contains the given substring.

    {"type": "notify_matches", "pattern": "..."}
        At least one notify() text matches the given regex.
    """

    def __init__(self, criteria_by_task: dict[str, list[dict[str, Any]]]) -> None:
        self._criteria = criteria_by_task
        self._notify_texts: list[str] = []
        # Enable buffering when any task has criteria so that notify()
        # calls are held until complete_task passes — user only receives
        # the final approved notification, not intermediate attempts.
        if any(v for v in criteria_by_task.values()):
            enable_buffer()

    def on_tool_call(self, name: str, arguments: dict) -> str | None:
        """Hook fn passed to tools.set_pre_execute_hook().

        Returns None to allow the call, or a non-empty string to block it
        and return that string as the tool result.
        """
        if name == "notify":
            text = arguments.get("text") or ""
            self._notify_texts.append(str(text))
            return None  # allow notify() — it self-buffers when buffer mode is on

        if name == "complete_task":
            task_id = arguments.get("task_id", "")
            criteria = self._criteria.get(task_id, [])
            if not criteria:
                flush_buffer()  # no guard — flush any buffered notifies now
                return None

            failures = self._check(criteria)
            if not failures:
                flush_buffer()  # criteria passed — deliver the final notification
                return None

            # Criteria failed — discard the queued notification so the agent
            # can call notify() again with improved content. Both the discarded
            # and new notify() texts remain visible in the event trace.
            clear_notify_buffer()
            msg = (
                f"BLOCKED by output_guard: complete_task('{task_id}') refused because "
                f"{len(failures)} success criterion/criteria failed:\n"
                + "\n".join(f"  • {f}" for f in failures)
                + "\n\nFix the above issues and retry. Your notify() must satisfy all "
                "criteria before this task can be marked complete."
            )
            return msg

        return None  # all other tools pass through unmodified

    def _check(self, criteria: list[dict[str, Any]]) -> list[str]:
        """Return a list of human-readable failure descriptions."""
        combined = " ".join(self._notify_texts)
        failures = []

        for c in criteria:
            ctype = c.get("type", "")

            if ctype == "notify_called":
                if not self._notify_texts:
                    failures.append("notify() was never called — the user received nothing")

            elif ctype == "notify_min_chars":
                n = int(c.get("n", 0))
                length = len(combined)
                if length < n:
                    failures.append(
                        f"notify text too short ({length} chars, need ≥ {n}) — "
                        "add the full problem statement and solution"
                    )

            elif ctype == "notify_has_code":
                if "```" not in combined:
                    failures.append(
                        "notify text contains no code block (```) — "
                        "include a working code solution"
                    )

            elif ctype == "notify_contains":
                required = c.get("text", "")
                if required and required not in combined:
                    failures.append(
                        f"notify text does not contain required string: {required!r}"
                    )

            elif ctype == "notify_matches":
                pattern = c.get("pattern", "")
                if pattern and not re.search(pattern, combined, re.IGNORECASE):
                    failures.append(
                        f"notify text does not match required pattern: {pattern!r}"
                    )

            else:
                # Unknown criterion type — skip rather than hard-fail so
                # adding new types doesn't break existing tasks.
                pass

        return failures


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
    # Snapshot due_at for each task so we can detect if complete_task ran.
    due_at_before = {t["id"]: t.get("due_at") for t in due_tasks}

    # Install output guard for this tick. The guard intercepts notify() and
    # complete_task() to enforce each task's success_criteria before allowing
    # completion. Always cleared in the finally block.
    guard = TaskGuard({
        t["id"]: t.get("success_criteria") or []
        for t in due_tasks
    })
    tools.set_pre_execute_hook(guard.on_tool_call)

    started = datetime.now()
    try:
        response = agent.chat(prompt)
    except Exception as e:
        # If the agent loop crashed AFTER complete_task already ran (e.g.,
        # the final text-generation LLM call failed), don't record a failure —
        # the task was delivered; we'd only be inflating consecutive_failures
        # on a task that will correctly fire again tomorrow.
        duration = (datetime.now() - started).total_seconds()
        err = f"{type(e).__name__}: {e}"
        for task in due_tasks:
            try:
                current = tasks.get(task["id"])
                if current and current.get("due_at") != due_at_before.get(task["id"]):
                    # due_at advanced → complete_task ran; skip failure recording.
                    print(
                        f"[heartbeat] {task['id']} due_at advanced — task was completed; "
                        f"skipping record_failure",
                        flush=True,
                    )
                    continue
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
    finally:
        tools.set_pre_execute_hook(None)
    print(f"[agent] {response}", flush=True)


def _format_due_tasks(tasks: list[dict]) -> str:
    import json as _json
    lines = []
    for task in tasks:
        block = (
            f"- id: {task.get('id')}\n"
            f"  title: {task.get('title')}\n"
            f"  due_at: {task.get('due_at')}\n"
            f"  recurrence: {task.get('recurrence', 'none')}\n"
            f"  notify: {task.get('notify', False)}\n"
            f"  description: {task.get('description', '')}"
        )
        criteria = task.get("success_criteria") or []
        if criteria:
            block += f"\n  success_criteria: {_json.dumps(criteria)}"
        lines.append(block)
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
