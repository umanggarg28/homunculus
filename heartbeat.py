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
from tools.notify import _send_to_telegram


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


REFLECTION_PROMPT_TEMPLATE = """It's a daily REFLECTION tick. Current date: {today}.

You have two jobs: (1) refine skills based on real task outcomes, (2) save new memories from yesterday's log.

━━ STEP 1 — Skill refinement (MANDATORY, do this first) ━━

read_file("tasks/tasks.json") and find every task that has `last_runs` entries from {yesterday}.

For each such task:
  a) Look in your memory index for a matching skill (name like `skill_<slug-of-title>`).
  b) If the last_runs show failures or anomalies (errors, BLOCKED, stuck loops, too many retries):
     - read_file the current skill memory.
     - Identify what went wrong from the `result` field of the failing run.
     - Update the skill with a corrected step or a "Watch out:" note.
     - Call remember() with the SAME name to overwrite — no new memory, same name.
  c) If the last_runs show only success AND no skill exists yet → write a new one.
  d) If the last_runs show only success AND a skill exists → no action needed.

Do not skip this step even if runs look successful on the surface — check the result text for errors, 403s, stuck loops, BLOCKED messages, or excessive retries.

━━ STEP 2 — Learn from yesterday's log ━━

read_file("memory/logs/{yesterday_path}.md")
If the file doesn't exist, skip to Step 3.

Look for:
- User corrections → save as "feedback" memory
- Ongoing goals or project state → save as "project" memory
- Confirmed good choices → save as "feedback" memory

━━ STEP 3 — Save memories + hygiene ━━

Save AT MOST 2 new memories. Use remember() with the SAME name to update existing ones — no duplicates.
Call forget() AT MOST 2 times for stale or contradicted memories.

━━ STEP 4 — Reply ━━

One line: what you learned or changed. If you updated a skill, say which one and why.

Rules: no notify(), no shell_exec, no writing workspace files, no reading logs other than yesterday's.
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
        # When any task has criteria, intercept notify() calls in the hook
        # itself — block them from reaching the MCP subprocess and hold the
        # text here, then send directly (bypassing subprocess boundary) once
        # complete_task passes all criteria.
        self._buffering = any(v for v in criteria_by_task.values())

    def on_tool_call(self, name: str, arguments: dict) -> str | None:
        """Hook fn passed to tools.set_pre_execute_hook().

        Returns None to allow the call, or a non-empty string to block it
        and return that string as the tool result.
        """
        if name == "notify":
            text = arguments.get("text") or ""
            self._notify_texts.append(str(text))
            if self._buffering:
                # Block the MCP call — we'll send directly from this process
                # once complete_task passes criteria, bypassing the subprocess.
                return f"Notification queued ({len(text)} chars) — will be delivered when task completes successfully."
            return None  # no criteria — let notify go through normally

        if name == "complete_task":
            task_id = arguments.get("task_id", "")
            criteria = self._criteria.get(task_id, [])
            if not criteria:
                self._flush()  # no guard — send any buffered notifies now
                return None

            failures = self._check(criteria)
            if not failures:
                self._flush()  # criteria passed — deliver buffered notifications
                return None

            # Criteria failed — discard queued notifications so the agent
            # retries notify() with improved content before the next attempt.
            self._notify_texts.clear()
            msg = (
                f"BLOCKED by output_guard: complete_task('{task_id}') refused because "
                f"{len(failures)} success criterion/criteria failed:\n"
                + "\n".join(f"  • {f}" for f in failures)
                + "\n\nFix the above issues and retry. Your notify() must satisfy all "
                "criteria before this task can be marked complete."
            )
            return msg

        return None  # all other tools pass through unmodified

    def _flush(self) -> None:
        """Send all buffered notifications directly (bypasses MCP subprocess).

        Only sends when _buffering=True, i.e. notify() was intercepted and
        held back from the MCP subprocess. When _buffering=False the MCP
        subprocess already delivered the notifications; calling this would
        double-send, so we just clear the tracking list.
        """
        if not self._buffering:
            self._notify_texts.clear()
            return
        for text in self._notify_texts:
            err = _send_to_telegram(text)
            if err:
                print(f"[TaskGuard] notify flush failed: {err}", flush=True)
                events.emit("tool_result", name="notify", result=f"ERROR: buffered notification failed to send: {err}")
            else:
                events.emit("tool_result", name="notify", result=f"Notification delivered ({len(text)} chars).")
        self._notify_texts.clear()

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

    Due tasks always take priority. Only after handling all due tasks does the
    heartbeat consider reflection (once per calendar day). This prevents the
    reflection branch from starving overdue tasks on the first tick of a new day.
    """
    now_iso = datetime.now().isoformat(timespec="seconds")
    tasks = TaskStore(Path(os.environ.get("HOMUNCULUS_TASKS_DIR", "./tasks")))

    # Auto-recover stale executing flags before computing due_tasks. The
    # main() startup cleanup only runs on container restart; if the agent
    # finishes without calling complete_task or record_failure (e.g.,
    # provider-exhaustion limp-along where the loop returns a fallback
    # string instead of raising), the flag stays True forever and the
    # task is filtered from due(). Anything older than the stale window
    # is forcibly cleared so the next tick can re-fire it.
    STALE_EXECUTING_SEC = 10 * 60
    now_dt = datetime.now()
    for t in tasks.all():
        if not t.get("executing"):
            continue
        last_fired = t.get("last_fired_at")
        if not last_fired:
            continue
        try:
            age = (now_dt - datetime.fromisoformat(last_fired)).total_seconds()
        except ValueError:
            continue
        if age > STALE_EXECUTING_SEC:
            try:
                tasks.record_failure(
                    t["id"],
                    f"executing flag stale ({int(age)}s old) — auto-cleared",
                    increment_failures=False,
                )
                print(
                    f"[heartbeat] auto-cleared stale executing flag on {t['id']!r} "
                    f"(age={int(age)}s)",
                    flush=True,
                )
            except Exception as _e:
                print(f"[heartbeat] auto-clear failed for {t['id']}: {_e}", flush=True)

    due_tasks = tasks.due()
    events.emit("service_ping", name="heartbeat", text="alive")

    if due_tasks:
        # Tasks take priority — fall through to the task-execution block below.
        pass
    else:
        # No due tasks — consider running the daily reflection instead.
        today = _today_str()
        last = memory.get_last_reflection_date()
        do_reflection = last is None or last < today
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
        # Provider exhaustion is a transient infrastructure failure — the task
        # itself is not broken. Don't count it toward consecutive_failures so
        # provider outages during peak hours don't auto-cancel healthy tasks.
        is_provider_exhaustion = "All providers exhausted" in err
        for task in due_tasks:
            try:
                current = tasks.get(task["id"])
                if current and current.get("due_at") != due_at_before.get(task["id"]):
                    print(
                        f"[heartbeat] {task['id']} due_at advanced — task was completed; "
                        f"skipping record_failure",
                        flush=True,
                    )
                    continue
                if is_provider_exhaustion:
                    # Log the failure but don't increment consecutive_failures.
                    print(
                        f"[heartbeat] {task['id']} provider-exhaustion failure — "
                        f"logging but not counting toward auto-cancel",
                        flush=True,
                    )
                    tasks.record_failure(task["id"], err, duration_s=duration,
                                        increment_failures=False)
                    events.emit(
                        "task_failure",
                        name=task["id"],
                        text=events.truncate_preview(err),
                        result="provider_exhaustion · consecutive_failures unchanged",
                    )
                else:
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

    # Post-success completion check. agent.chat() returned without an
    # exception, but that doesn't mean the task was actually delivered —
    # under heavy provider rate-limiting the loop can drop into the
    # "I'm not sure how to respond — could you rephrase?" fallback path
    # without ever calling notify() or complete_task(). When that
    # happens, due_at stays put and executing=True remains stuck.
    # Detect by checking whether due_at advanced (the dedupe signal we
    # already use in the failure handler) and record_failure on any
    # task whose due_at is unchanged so the next tick can pick it up.
    for task in due_tasks:
        try:
            current = tasks.get(task["id"])
            if current is None:
                continue
            if current.get("due_at") != due_at_before.get(task["id"]):
                continue  # complete_task ran, advanced due_at — good
            # Task didn't complete. Record as a failure so executing clears.
            # Treat as provider-exhaustion-like (don't penalise the task)
            # because the most common cause is the LLM degrading mid-loop.
            print(
                f"[heartbeat] {task['id']} returned without complete_task — "
                f"recording soft failure to clear executing flag",
                flush=True,
            )
            tasks.record_failure(
                task["id"],
                "agent loop returned without complete_task — likely provider degradation",
                duration_s=(datetime.now() - started).total_seconds(),
                increment_failures=False,
            )
            events.emit(
                "task_failure",
                name=task["id"],
                text="returned without complete_task",
                result="executing flag cleared · consecutive_failures unchanged",
            )
        except Exception as inner:
            print(f"[heartbeat] post-tick check failed for {task['id']}: {inner}", flush=True)


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

    dropped = events.rotate(keep_days=14)
    if dropped:
        print(f"[heartbeat] rotated _events.jsonl: dropped {dropped} lines older than 14 days", flush=True)

    # Crash recovery: any task left with executing=True from a previous run
    # is stuck and will never fire again. Clear the flag on startup so the
    # next tick can pick them up.
    _tasks_dir = Path(os.environ.get("HOMUNCULUS_TASKS_DIR", "./tasks"))
    _task_store = TaskStore(_tasks_dir)
    _stuck = [t for t in _task_store.all() if t.get("executing")]
    for _t in _stuck:
        try:
            _task_store.record_failure(
                _t["id"],
                "cleared by heartbeat restart — previous run did not finish",
                increment_failures=False,
            )
            print(f"[heartbeat] cleared stuck executing flag on task {_t['id']!r}", flush=True)
        except Exception as _e:
            print(f"[heartbeat] could not clear executing on {_t['id']}: {_e}", flush=True)

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
        memory.set_next_tick(wake_at)
        _interruptible_sleep(sleep_seconds)
        memory.pop_next_tick()  # consumed — clear so stale value doesn't persist after waking


def _interruptible_sleep(total_seconds: float, poll_interval: float = 60.0) -> None:
    """Sleep for `total_seconds` but wake early if a task becomes due.

    Polls once per `poll_interval` (default 60s). When a task's due_at
    is <= now the loop breaks immediately so the next tick fires without
    waiting out the rest of the sleep window. This fixes the missed-
    notification problem: tasks created mid-sleep are picked up within
    one poll interval instead of the full default sleep window.
    No LLM cost — the due() check is a local JSON read.
    """
    task_store = TaskStore(Path(os.environ.get("HOMUNCULUS_TASKS_DIR", "./tasks")))
    deadline = time.monotonic() + total_seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(poll_interval, remaining))
        if task_store.due():
            print("[heartbeat] task became due mid-sleep — waking early", flush=True)
            break


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
