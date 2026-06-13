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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

import agent_controls
import events
import tools
from core import Agent, measure_llm_usage_since
from memory import Memory
from tasks import TaskStore, clear_scratchpad
from tools.notify import _send_to_telegram


HEARTBEAT_PROMPT_TEMPLATE = """It's a scheduled heartbeat tick — no user is
talking to you right now. The current time is {now_iso}.

Python already checked structured task state and found these due tasks:

{due_tasks}

Handle ONLY the due task(s). Do not invent unrelated proactive work.

Useful proactive actions:
- For notification tasks, use `notify()` with a concise message.
- For research/delivery tasks, do the minimal needed work and save or
  notify the result as the task description asks.

Close every due task with exactly one of:
  ✓ complete_task(task_id, result)        — delivered cleanly
  ↻ continue_task(task_id, reason,        — partial progress; resume next tick
                  scratchpad_update=...)
  ✗ record_failure(task_id, reason)       — couldn't do it (broken source, missing
                                            data); a recurring task resumes at its
                                            NEXT scheduled occurrence — for transient
                                            provider throttling use continue_task
                                            instead (retries in ~10 min)
  ✗ cancel_task(task_id, reason)          — give up entirely (task is wrong/done/obsolete)

Continuation pattern: if you're partway through and hitting the iteration
budget OR a provider just throttled, call continue_task() with a one-line
scratchpad summary of what's done. Read task_scratchpad(task_id) at the
START of any task — if there's content from a prior run, resume from there
instead of starting over.

Success criteria: some tasks list `success_criteria` — machine-checked rules
on your notify() text. If they fail, notify() returns a BLOCKED message
explaining what's missing; fix the content and call notify() again.
complete_task() is refused with an ERROR until the criteria pass — if you
genuinely cannot deliver, call record_failure() instead of trying to close
the task. Tasks may list `already_delivered` keys: NEVER send any of those
again; pick the next undelivered item.

An EMPTY or no-content delivery is NOT a clean completion. If the core
deliverable is missing — no items found, the source returned nothing, a
fetch failed — call record_failure(task_id, reason), NOT complete_task
with a "nothing found today" message. A recorded failure triggers skill
refinement next reflection tick (the skill that produced nothing gets
fixed); a fake "nothing found" success hides the gap and the skill stays
broken forever. Only complete_task when you actually delivered the thing
the task is for.

Scheduling: default next tick is in ~60 minutes (or sooner if another task
is due). Call `schedule_next_tick("YYYY-MM-DDTHH:MM:SS")` to wake at a
specific time (must be future, within 24h).

Important rules:
- DO NOT read the daily log files unless a task asks for it. Logs contain
  your own previous output; reading them every tick creates a feedback loop.
- shell_exec is disabled. If a task needs shell access, call remember()
  to leave a note for the user.
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
     - Prepare the FULL corrected body (a fixed step or a "Watch out:" note);
       keep everything you didn't change verbatim, especially code, queries
       and URLs.
     - Call propose_skill(name="skill_<slug>", body=<full corrected body>,
       rationale=<what failed and how this fixes it>, kind="skill_edit").
       This does NOT change the skill now — it files the edit for the
       operator to approve in the dashboard. Do not write_file the skill
       directly; self-modifications go through review.
  c) If the last_runs show only success AND no skill exists yet → propose one
     with propose_skill(..., kind="new_skill").
  d) If the last_runs show only success AND a skill exists → no action needed.

After proposing, the skill stays on its current body until approved — do
not assume your edit is live this tick.

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
When a memory relates to one you already have, reference it inline as [[its-name]] in the body — cross-links are how recall finds context later.
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

    {"type": "notify_unique", "pattern": "..."}
        The notify text must contain a delivery key (regex group 1, or
        the whole match) that is NOT in the task's `delivered` ledger.
        Blocks re-sending content the user already received — checked
        deterministically at send time, not entrusted to the model's
        bookkeeping. The heartbeat records the key into the ledger via
        TaskStore.record_delivery once the notify has gone out.
    """

    def __init__(
        self,
        criteria_by_task: dict[str, list[dict[str, Any]]],
        delivered_by_task: dict[str, set[str]] | None = None,
    ) -> None:
        self._criteria = criteria_by_task
        # Lowercased delivery keys each task has already sent (from the
        # task's `delivered` ledger). Consulted by notify_unique.
        self._delivered = delivered_by_task or {}
        # Notify texts seen so far this tick — kept as a tracker only, NOT
        # as a delivery buffer. Every notify() call now sends immediately;
        # this list lets _check() validate criteria pre-send and lets
        # expected_remaining() report whether the agent ever called notify
        # at all. Previously the TaskGuard buffered notify and gated on
        # complete_task succeeding — which silently dropped every delivery
        # when the agent wrote "task complete" in prose instead of calling
        # the tool. The structural fix is: criteria are checked SYNCHRONOUSLY
        # at notify() call time. If they fail, notify is refused with a
        # retry instruction. The agent never gets to act on a buffered
        # message that might not deliver.
        self._notify_texts: list[str] = []
        # Track which due tasks have had complete_task called successfully.
        # Combined with expected_remaining(), the heartbeat tick can detect
        # the silent-failure mode (agent loop ran but no due task was
        # completed) and report a *specific* failure.
        self._completed_tasks: set[str] = set()

    def on_tool_call(self, name: str, arguments: dict) -> str | None:
        """Hook fn passed to tools.set_pre_execute_hook().

        Returns None to allow the call, or a non-empty string to block it
        and return that string as the tool result.
        """
        if name == "notify":
            text = str(arguments.get("text") or "")
            # Check criteria across ALL due tasks. If any task's criteria
            # would fail with the combined text so far + this proposed
            # message, refuse the send and tell the agent what's missing.
            # This is the structural fix for "the buffered message died
            # when the agent skipped complete_task" — now nothing gets
            # buffered. Either notify delivers and counts, or it's blocked
            # and the agent retries before the loop ends.
            #
            # Criteria are evaluated against the combined notify texts
            # because some criteria (notify_min_chars, notify_has_code)
            # can be satisfied by an earlier notify in the same tick
            # plus this one — the historical behavior we want to preserve.
            tentative = self._notify_texts + [text]
            failures = self._check_against(tentative)
            if failures:
                return (
                    f"BLOCKED: notify() not sent. Criteria failed:\n"
                    + "\n".join(f"  • {f}" for f in failures)
                    + "\n\nCall notify(text=...) again with content that "
                    "satisfies the failed criterion (e.g. add a fenced "
                    "```code block```, lengthen the message, include the "
                    "required substring). The retry will be sent if it "
                    "passes."
                )
            # Criteria pass — record the text and let the call through.
            # The MCP subprocess does the actual Telegram send.
            self._notify_texts.append(text)
            return None

        if name == "complete_task":
            task_id = arguments.get("task_id", "")
            # Gate completion on the task's own criteria. notify()-time
            # checking (above) covers the "bad content" case, but nothing
            # stopped the model from calling complete_task WITHOUT ever
            # calling notify — observed live 2026-06-11: "could not fetch
            # problem ...; task marked complete" closed the task with the
            # user receiving nothing. The result string must start with
            # "ERROR" — core.py's terminal-tool accounting treats any
            # other prefix as a successful close and exits the loop.
            failures = self.criteria_failures(task_id)
            if failures:
                return (
                    "ERROR: complete_task blocked — this task's delivery "
                    "criteria are not satisfied:\n"
                    + "\n".join(f"  • {f}" for f in failures)
                    + "\n\nDeliver the content with notify(text=...) first, "
                    "then call complete_task again. If delivery is impossible "
                    "right now, call record_failure(task_id, reason) instead. "
                    "Do NOT mark this task complete without delivering."
                )
            self._completed_tasks.add(task_id)
            return None

        # The other lifecycle tools also close out a task — track them so
        # the post-tick check doesn't treat an explicit close as a silent
        # drop (which would double-record a partial on the same run).
        if name in ("record_failure", "cancel_task", "continue_task"):
            task_id = arguments.get("task_id", "")
            if task_id:
                self._completed_tasks.add(task_id)
            return None

        return None  # all other tools pass through unmodified

    def criteria_failures(self, task_id: str) -> list[str]:
        """Failure descriptions for ONE task's criteria, checked against
        the notify texts actually sent this tick. Empty list = satisfied.
        Tasks with no criteria always pass (there is nothing to check)."""
        return self._check(self._criteria.get(task_id) or [], task_id=task_id)

    def delivery_key(self, task_id: str) -> str | None:
        """The delivery key contained in this tick's SENT notify texts,
        per the task's notify_unique criterion. None when the task has
        no notify_unique criterion or nothing matched. Sent texts are
        ground truth for what reached the user — this is what the
        heartbeat records into the task's `delivered` ledger."""
        combined = " ".join(self._notify_texts)
        for c in self._criteria.get(task_id) or []:
            if c.get("type") == "notify_unique" and c.get("pattern"):
                m = re.search(c["pattern"], combined, re.IGNORECASE)
                if m:
                    return (m.group(1) if m.groups() else m.group(0)).strip().lower()
        return None

    def expected_remaining(self) -> list[str]:
        """Task IDs that were due at the start of this tick but have not yet
        had complete_task (or record_failure) called.

        Used by heartbeat.tick() after the agent loop returns to produce a
        more specific diagnostic than the generic post-success check — and
        to drive the autonomous notify() pattern from item 8 of the plan.
        """
        return [tid for tid in self._criteria if tid not in self._completed_tasks]

    def on_pre_turn(self, turn_idx: int, _history: list) -> dict | None:
        """Pre-turn hook (item 5 of robustness plan).

        Installed via tools.set_pre_turn_hook(). Called at the start of
        every loop iteration with the 0-indexed turn number. Returns a
        synthetic user message to inject, or None for a no-op turn.

        Currently used for ONE thing: at iter MAX_TURNS-1 (turn 19 of 20),
        if any due task is still unfinished, force a final message demanding
        complete_task() or record_failure() before the loop hits the cap.
        This is the structural complement to the prompt tightening — instead
        of HOPING the model wraps up, the harness ORDERS it to.

        Imported locally to avoid a circular import at module load.
        """
        from config import get_config  # local import — see docstring
        max_turns = get_config().loop.max_turns
        if turn_idx != max_turns - 1:
            return None
        remaining = self.expected_remaining()
        if not remaining:
            return None
        # Pick the first uncompleted task — the message is per-task explicit.
        task_id = remaining[0]
        return {
            "role": "user",
            "content": (
                f"HARNESS DIRECTIVE (last iteration): task '{task_id}' has "
                f"not yet had complete_task() OR record_failure() called. "
                f"You have exactly one tool call left.\n\n"
                f"Pick ONE of:\n"
                f"  ✓ complete_task(task_id='{task_id}', result='<one-line summary>')\n"
                f"  ✗ record_failure(task_id='{task_id}', reason='<one-line reason>')\n\n"
                f"Notifications you've already sent this tick have already "
                f"reached the user — complete_task just closes the lifecycle. "
                f"If you haven't been able to deliver useful content yet, "
                f"prefer record_failure with the reason. DO NOT call any "
                f"other tool."
            ),
        }

    def _check_against(self, texts: list[str]) -> list[str]:
        """Check all due tasks' criteria against a candidate notify-text list.

        Aggregates failures across every task that has criteria. The
        agent sees a single combined "you need X, Y, Z" message so it
        can craft one corrected notify call covering all of them rather
        than trial-and-erroring per criterion.

        Called from on_tool_call pre-send: `texts = self._notify_texts +
        [proposed_text]`. The candidate text is delivered only when
        this returns an empty list.
        """
        failures: list[str] = []
        for task_id, criteria in self._criteria.items():
            failures.extend(self._check(criteria, texts, task_id=task_id))
        return failures

    def _check(
        self,
        criteria: list[dict[str, Any]],
        texts: list[str] | None = None,
        task_id: str = "",
    ) -> list[str]:
        """Return a list of human-readable failure descriptions for one
        task's criteria. `texts` defaults to the recorded notify texts
        (for the silent-drop fallback path); pre-send checking passes
        an explicit candidate list. `task_id` keys the delivered-ledger
        lookup for notify_unique."""
        if texts is None:
            texts = self._notify_texts
        combined = " ".join(texts)
        failures = []

        for c in criteria:
            ctype = c.get("type", "")

            if ctype == "notify_called":
                if not texts:
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

            elif ctype == "notify_unique":
                pattern = c.get("pattern", "")
                if pattern:
                    m = re.search(pattern, combined, re.IGNORECASE)
                    if not m:
                        failures.append(
                            f"notify text contains no delivery key matching "
                            f"{pattern!r} — include the canonical link/identifier"
                        )
                    else:
                        key = (m.group(1) if m.groups() else m.group(0)).strip().lower()
                        if key in self._delivered.get(task_id, set()):
                            failures.append(
                                f"{key!r} was already delivered on a previous "
                                f"run — send the NEXT undelivered item instead "
                                f"(the task block lists already_delivered keys)"
                            )

            else:
                # Unknown criterion type — skip rather than hard-fail so
                # adding new types doesn't break existing tasks.
                pass

        return failures


# User TZ is autodetected from the browser (see user_tz module) — no env
# var, no hardcoding. The browser writes workspace/user_tz.txt on its first
# visit; this module reads from there and falls back to system local.
from user_tz import now_user_tz as _now_user_tz, now_user_naive as _now_user_naive  # noqa: E402


def _today_str() -> str:
    return _now_user_tz().strftime("%Y-%m-%d")


def _yesterday_iso_and_path() -> tuple[str, str]:
    """Return (YYYY-MM-DD, YYYY/MM/YYYY-MM-DD) for yesterday in user TZ."""
    y = _now_user_tz() - timedelta(days=1)
    iso = y.strftime("%Y-%m-%d")
    path_form = y.strftime("%Y/%m/%Y-%m-%d")
    return iso, path_form


def tick(memory: Memory, model: str | None) -> None:
    """One heartbeat iteration — fresh agent, one prompt, then discard.

    Due tasks always take priority. Only after handling all due tasks does the
    heartbeat consider reflection (once per calendar day). This prevents the
    reflection branch from starving overdue tasks on the first tick of a new day.
    """
    # Kill switch — checked before ANY work, including stale-flag
    # recovery. While paused the heartbeat is inert: no task execution,
    # no reflection, no LLM spend, no state mutation. Chat is unaffected
    # (the switch halts autonomy, not conversation).
    if agent_controls.load_controls().paused:
        print("[heartbeat] HALTED by operator kill switch — tick skipped", flush=True)
        return

    # Use user-TZ-aware now for the prompt — the agent quotes this back to
    # the user, so naive UTC here causes "the current time is 06:30 IST"
    # mismatches in chat replies.
    now_iso = _now_user_tz().isoformat(timespec="seconds")
    tasks = TaskStore(Path(os.environ.get("HOMUNCULUS_TASKS_DIR", "./tasks")))

    # Auto-recover stale executing flags before computing due_tasks. The
    # main() startup cleanup only runs on container restart; if the agent
    # finishes without calling complete_task or record_failure (e.g.,
    # provider-exhaustion limp-along where the loop returns a fallback
    # string instead of raising), the flag stays True forever and the
    # task is filtered from due(). Anything older than the stale window
    # is forcibly cleared so the next tick can re-fire it.
    STALE_EXECUTING_SEC = 10 * 60
    # Use user-local naive — last_fired_at is written by tasks.py in the
    # user's wall-clock TZ. Mixing container UTC here would treat
    # recently-fired tasks as stale on UTC containers in non-UTC zones.
    now_dt = _now_user_naive()
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
        last = memory.reflection.last_date()
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
            response = agent.chat(prompt, source="heartbeat")
            memory.reflection.mark(today)
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

    memory_root = Path(os.environ.get("HOMUNCULUS_MEMORY_DIR", "./memory"))
    state_sequence, selected_tasks, playbooks = _plan_tick(due_tasks, memory_root)

    # Stamp `last_fired_at` BEFORE running the agent — only on tasks
    # we actually attempt this tick. Deferred state-machine tasks must
    # NOT be marked fired here, otherwise the re-fire suppression
    # window would skip them on the next tick.
    for task in selected_tasks:
        try:
            tasks.mark_fired(task["id"])
        except Exception as e:
            print(f"[heartbeat] mark_fired failed for {task['id']}: {e}", flush=True)

    due_tasks_block = _format_due_tasks(selected_tasks)
    if playbooks:
        due_tasks_block += "\n\n" + "\n\n".join(playbooks)
    prompt = HEARTBEAT_PROMPT_TEMPLATE.format(
        now_iso=now_iso,
        due_tasks=due_tasks_block,
    )
    # Snapshot due_at for each task so we can detect if complete_task ran.
    due_at_before = {t["id"]: t.get("due_at") for t in selected_tasks}

    # Install output guard for this tick. The guard intercepts notify() and
    # complete_task() to enforce each task's success_criteria before allowing
    # completion. Always cleared in the finally block.
    guard = TaskGuard(
        {t["id"]: t.get("success_criteria") or [] for t in selected_tasks},
        delivered_by_task={
            t["id"]: {d.get("key", "") for d in (t.get("delivered") or [])}
            for t in selected_tasks
        },
    )
    tools.set_pre_execute_hook(guard.on_tool_call)
    # Item 5 (pragmatic slice): install the turn-level hook so the guard
    # can inject a forced completion message at iter MAX_TURNS-1 when any
    # due task is still unfinished.
    tools.set_pre_turn_hook(guard.on_pre_turn)

    started = datetime.now()
    # Wall-clock UTC for events.jsonl scan; events log timestamps are UTC.
    started_utc = datetime.now(timezone.utc)
    try:
        response = agent.chat(
            prompt,
            source="heartbeat",
            state_sequence=state_sequence,
            expected_completions=len(selected_tasks),
        )
    except Exception as e:
        # If the agent loop crashed AFTER complete_task already ran (e.g.,
        # the final text-generation LLM call failed), don't record a failure —
        # the task was delivered; we'd only be inflating consecutive_failures
        # on a task that will correctly fire again tomorrow.
        # Notifies sent before the crash DID reach the user — record their
        # delivery keys so the retry doesn't re-send the same content.
        _record_delivery_keys(tasks, guard, selected_tasks)
        duration = (datetime.now() - started).total_seconds()
        err = f"{type(e).__name__}: {e}"
        # Infrastructure failures (provider exhaustion, provider API
        # errors, network blips) are transient — the task itself is not
        # broken. Mark partial → retry in ~10 min, don't count toward
        # consecutive_failures, and crucially don't advance due_at a
        # whole recurrence step. Observed live 2026-06-11: a provider
        # 404 at 09:00 was recorded as a REAL failure, which advanced
        # the daily task to tomorrow and silently skipped the day's
        # delivery. Only non-infra exceptions (actual task/code bugs)
        # take the record_failure path now.
        is_provider_exhaustion = _is_infra_error(err)
        for task in selected_tasks:
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
                    # Provider exhaustion is a partial — work was in
                    # flight when the infrastructure ran out, not a
                    # broken task. mark_partial reschedules ~10 min
                    # later so the same provider's TPM window can
                    # refresh; the scratchpad survives.
                    print(
                        f"[heartbeat] {task['id']} provider-exhaustion — "
                        f"marking partial, will retry shortly",
                        flush=True,
                    )
                    usage = measure_llm_usage_since(started_utc)
                    tasks.mark_partial(task["id"], err, duration_s=duration, usage=usage)
                    events.emit(
                        "task_partial",
                        name=task["id"],
                        text=events.truncate_preview(err),
                        result="provider_exhaustion · retry in ~10 min",
                    )
                else:
                    usage = measure_llm_usage_since(started_utc)
                    updated = tasks.record_failure(task["id"], err, duration_s=duration, usage=usage)
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
        tools.set_pre_turn_hook(None)
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
    # Item 4: use guard.expected_remaining() to distinguish "agent explicitly
    # called record_failure" (intentional fail with reason — don't double-log)
    # from "agent never touched the task" (silent fall-through — the bug
    # this whole layer exists to catch).
    silently_dropped = set(guard.expected_remaining())
    # Sent notifies are ground truth for what reached the user — record
    # delivery keys regardless of how the lifecycle ended, so no future
    # run can re-send the same content.
    _record_delivery_keys(tasks, guard, selected_tasks)
    # Measure once for the whole tick — attribute to each task that
    # ran. When multiple tasks share a tick the attribution is
    # over-counted; in practice most ticks fire one task at a time.
    tick_usage = measure_llm_usage_since(started_utc)
    for task in selected_tasks:
        try:
            current = tasks.get(task["id"])
            if current is None:
                continue
            if current.get("due_at") != due_at_before.get(task["id"]):
                # complete_task ran, due_at advanced. Retrofit usage
                # onto the success run that the tool layer appended.
                tasks.attribute_usage_to_last_run(task["id"], tick_usage)
                continue
            # Distinguish the failure shape so we can act on it differently:
            #   - silent drop  → agent never called complete_task or record_failure.
            #                    The post-success check has to clean up.
            #   - explicit fail → agent called record_failure with a reason
            #                     (which the TaskGuard tracked). The reason is
            #                     already in the task's last_runs; don't double-log.
            if task["id"] not in silently_dropped:
                # Agent explicitly closed the task (record_failure /
                # cancel_task / continue_task) — respect its decision.
                continue
            _settle_silent_drop(
                tasks,
                task,
                guard,
                duration_s=(datetime.now() - started).total_seconds(),
                usage=measure_llm_usage_since(started_utc),
            )
        except Exception as inner:
            print(f"[heartbeat] post-tick check failed for {task['id']}: {inner}", flush=True)


def _plan_tick(
    due_tasks: list[dict[str, Any]],
    memory_root: Path,
) -> tuple[list[dict] | None, list[dict[str, Any]], list[str]]:
    """Decide what this tick runs and which playbooks reach the prompt.

    Returns (state_sequence, selected_tasks, playbook_blocks).

    A task linked to a skill with a `states:` frontmatter declaration
    runs EXCLUSIVELY this tick — state_sequence pins turns 0..N-1, so
    if the model handled a different task first the pinned turns would
    fire on the wrong task. Other due tasks defer to the next tick.

    Stateless skills contribute their playbook body to the prompt.
    Previously the body was injected ONLY for state-machine skills —
    a task linked to an ordinary skill ran with no playbook at all,
    so the model improvised from web_search instead of following its
    own instructions (observed live 2026-06-11: delivered an algomap.io
    link for a task whose playbook says LeetCode GraphQL only).
    """
    from skills import load_skill_playbook

    playbooks: list[str] = []
    for t in due_tasks:
        skill_name = t.get("skill")
        if not skill_name:
            continue
        try:
            states, body = load_skill_playbook(memory_root, skill_name)
        except FileNotFoundError:
            print(
                f"[heartbeat] {t['id']!r} skill {skill_name!r} not found; "
                f"falling back to free-form",
                flush=True,
            )
            continue
        block = (
            f"# Playbook for task '{t['id']}' "
            f"(auto-loaded from {skill_name})\n\n{body.strip()}"
        )
        if states:
            print(
                f"[heartbeat] {t['id']!r} → state machine ({len(states)} states) "
                f"from {skill_name!r}; deferring {len(due_tasks) - 1} other due task(s)",
                flush=True,
            )
            return states, [t], [block]
        playbooks.append(block)
    return None, due_tasks, playbooks


def _record_delivery_keys(
    tasks: TaskStore,
    guard: TaskGuard,
    selected_tasks: list[dict[str, Any]],
) -> None:
    """Persist this tick's delivery keys into each task's ledger.

    Best-effort and idempotent — called on both the success and the
    crash path of a tick, because a notify that went out before a
    later exception still reached the user.
    """
    for task in selected_tasks:
        try:
            key = guard.delivery_key(task["id"])
            if key:
                tasks.record_delivery(task["id"], key)
        except Exception as e:
            print(f"[heartbeat] record_delivery failed for {task['id']}: {e}", flush=True)


def _settle_silent_drop(
    tasks: TaskStore,
    task: dict[str, Any],
    guard: TaskGuard,
    duration_s: float,
    usage: dict[str, Any],
) -> None:
    """Close out a due task the agent finished without an explicit
    lifecycle call (no complete_task / record_failure / cancel_task /
    continue_task).

    Two shapes, decided by the machine-checked criteria:

    - Delivery DID happen: every criterion passed on notify texts that
      actually went out, only the bookkeeping call is missing. The
      harness completes the task itself. Re-firing in this state is
      what used to send the user the same content twice — notify() is
      not buffered, so the message was already delivered.
    - No valid delivery: record a partial so the next tick retries from
      the scratchpad (plus skill auto-refinement and the escalation
      notify, unchanged).

    Pattern: the harness owns deterministic lifecycle transitions; the
    model only does the content work (Pi's guard layer, Claude Code's
    hooks). A task with no criteria can't prove delivery, so it always
    takes the partial path.
    """
    task_id = task["id"]
    criteria = task.get("success_criteria") or []
    if criteria and not guard.criteria_failures(task_id):
        tasks.complete(
            task_id,
            "auto-completed by harness: delivery criteria satisfied "
            "(agent omitted complete_task)",
        )
        clear_scratchpad(tasks.root, task_id)
        tasks.attribute_usage_to_last_run(task_id, usage)
        print(
            f"[heartbeat] {task_id} auto-completed — criteria satisfied, "
            f"agent omitted complete_task",
            flush=True,
        )
        events.emit(
            "task_complete",
            name=task_id,
            text="harness auto-complete: criteria satisfied; agent omitted complete_task",
        )
        return

    print(
        f"[heartbeat] {task_id} silently dropped — agent did not call "
        f"complete_task OR record_failure. Recording soft failure to clear "
        f"executing flag.",
        flush=True,
    )
    # Silent drop → mark partial (the work was in progress, the agent
    # just didn't call any termination tool). The next tick fires the
    # same task ~10 min later, and the scratchpad (if the skill wrote
    # to one) survives.
    updated = tasks.mark_partial(
        task_id,
        "silent drop — agent didn't call complete_task / "
        "continue_task / cancel_task",
        duration_s=duration_s,
        usage=usage,
    )
    events.emit(
        "task_partial",
        name=task_id,
        text="silent drop (no termination tool called)",
        result=(
            f"consecutive_partials={updated.get('consecutive_partials', '?')}"
            f" · retry in ~10 min"
        ),
    )
    # T1.2: skill auto-refinement. Append a Watch-out note to the
    # related skill_*.md so next-run's agent has the lesson available
    # without us having to manually update the skill. Best-effort.
    try:
        from tools._skill_refiner import update_skill_on_failure
        updated_path = update_skill_on_failure(
            task_id,
            "silent drop on a heartbeat run — agent reached the end of "
            "the loop without calling complete_task or record_failure. "
            "Either provider was degraded or the loop ran out of "
            "context before the final tool call. Make sure you call "
            "complete_task or record_failure even with partial data.",
            memory_dir=os.environ.get("HOMUNCULUS_MEMORY_DIR", "./memory"),
        )
        if updated_path:
            print(f"[heartbeat] auto-refined skill {updated_path}", flush=True)
    except Exception as refine_err:
        print(
            f"[heartbeat] skill auto-refine failed for {task_id}: {refine_err}",
            flush=True,
        )
    # Autonomous fallback notify — only when mark_partial ESCALATED to
    # a real failure (consecutive_failures > 0 after the call). Plain
    # partials are routine continuation state, not user-actionable, so
    # notifying on each would spam the user every 10 min. Refresh from
    # disk because mark_partial mutated the task.
    if task.get("notify"):
        refreshed = tasks.get(task_id) or {}
        escalated = int(refreshed.get("consecutive_failures", 0)) > 0
        if not escalated:
            print(
                f"[heartbeat] {task_id} partial — suppressing user "
                f"notification (will only fire on escalation)",
                flush=True,
            )
            return
        try:
            title = task.get("title") or task_id
            _send_to_telegram(
                f"⚠️ I tried '{title}' multiple times today and "
                f"couldn't get it through (provider limits or "
                f"task is broken). Pausing automatic retries; "
                f"check Traces if you want to know why."
            )
        except Exception as notify_err:
            print(
                f"[heartbeat] fallback-notify failed for {task_id}: {notify_err}",
                flush=True,
            )


def _format_due_tasks(tasks: list[dict]) -> str:
    import json as _json
    from tasks import read_scratchpad
    tasks_root = Path(os.environ.get("HOMUNCULUS_TASKS_DIR", "./tasks"))
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
        delivered = [d.get("key", "") for d in (task.get("delivered") or [])]
        if delivered:
            # The harness-owned ledger of what this task already sent.
            # Listed so the agent picks the next item WITHOUT having to
            # maintain its own tracker file; notify_unique enforces it
            # mechanically even if the agent ignores the list.
            recent = delivered[-40:]
            block += (
                "\n  already_delivered (never send these again): "
                + ", ".join(recent)
            )
        partials = int(task.get("consecutive_partials", 0))
        if partials > 0:
            block += f"\n  RESUMING — consecutive_partials: {partials}"
        scratch = read_scratchpad(tasks_root, task.get("id", ""))
        if scratch.strip():
            # Inline the scratchpad so the agent sees it in the same
            # prompt that lists the task — no separate read needed.
            # Capped at 2000 chars (the rest is available via
            # task_scratchpad() if it grew large) but typically 1-2
            # lines of "what I did last time".
            preview = scratch.strip()
            if len(preview) > 2000:
                preview = preview[:2000] + f"\n... [+{len(scratch) - 2000} more chars in scratchpad]"
            block += f"\n  scratchpad (from prior run):\n    " + preview.replace("\n", "\n    ")
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
    # Heartbeat default: openai/gpt-oss-120b (same as the chat primary).
    # See core.MODEL for why — single-model setup keeps reliability
    # reasoning consistent across heartbeat and chat. Override via
    # HOMUNCULUS_MODEL_HEARTBEAT in .env if you want to A/B test.
    model = os.environ.get("HOMUNCULUS_MODEL_HEARTBEAT", "openai/gpt-oss-120b")

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
    tick_failed = False
    while True:
        try:
            tick(memory, model=model)
            tick_failed = False
        except Exception as e:
            # Don't let one bad tick kill the daemon. Log and continue.
            print("[heartbeat] error during tick:", flush=True)
            traceback.print_exc()
            # Network-class errors (DNS, connect refused) are usually
            # transient — retry in 60s instead of waiting the full hour
            # backoff. Without this, a single DNS blip during a recurring
            # task's window loses the whole day's run.
            tick_failed = _is_transient_network_error(e)

        if tick_failed:
            sleep_seconds = 60.0
        else:
            sleep_seconds = _compute_sleep(memory, default_interval)
        # USER-naive, not container-naive: this string is read back by
        # _compute_sleep (user-naive comparison), served by
        # /api/agent/upcoming, and parsed by the browser as local time.
        # The old datetime.now() wrote UTC wall clock on Docker, which
        # the sidebar read as IST — 5.5h in the past, clamped to "0s".
        wake_at = (_now_user_naive() + timedelta(seconds=sleep_seconds)).isoformat(timespec="seconds")
        print(f"[heartbeat] sleeping {sleep_seconds:.0f}s, next tick ~{wake_at}", flush=True)
        memory.next_tick.set(wake_at)
        _interruptible_sleep(sleep_seconds)
        memory.next_tick.pop()  # consumed — clear so stale value doesn't persist after waking


def _is_infra_error(err: str) -> bool:
    """True when an agent-loop exception string describes infrastructure
    (LLM provider / network) trouble rather than a broken task.

    Infra errors mark the task PARTIAL (retry ~10 min, scratchpad
    survives); everything else records a real failure (advances a
    recurring task to its next occurrence and counts toward
    auto-cancel). The strings come from core.call_llm's raise sites
    and httpx exception names.
    """
    return any(
        marker in err
        for marker in (
            "All providers exhausted",   # call_llm chain fully cooled
            "API error",                 # call_llm non-2xx raise
            "ConnectError",
            "ConnectTimeout",
            "ReadTimeout",
            "RemoteProtocolError",
        )
    )


def _is_transient_network_error(exc: BaseException) -> bool:
    """True if exc is the kind of network blip worth retrying in 60s.

    DNS resolution failures, connect refused, and connect timeouts on
    the LLM provider call almost always clear within a minute. Backing
    off to the default hour drops a whole task window over what's
    usually a 30-second hiccup. We do NOT shortcut on 4xx/auth/quota
    errors — those don't fix themselves and the longer cooldown is correct.
    """
    # Walk the cause/context chain so wrapped httpx errors are caught.
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        name = type(cur).__name__
        msg = str(cur).lower()
        if name in {"ConnectError", "ConnectTimeout", "ReadTimeout"}:
            return True
        if "name resolution" in msg or "temporary failure" in msg:
            return True
        if "connection refused" in msg or "network is unreachable" in msg:
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def _interruptible_sleep(total_seconds: float, poll_interval: float = 60.0) -> None:
    """Sleep for `total_seconds` but wake early if a task becomes due.

    Polls once per `poll_interval` (default 60s). When a task's due_at
    is <= now the loop breaks immediately so the next tick fires without
    waiting out the rest of the sleep window. This fixes the missed-
    notification problem: tasks created mid-sleep are picked up within
    one poll interval instead of the full default sleep window.
    No LLM cost — the due() check is a local JSON read.

    Also emits a `service_ping` every ~10 minutes so /api/status keeps
    reporting heartbeat as "live" between hourly ticks. Without this,
    a healthy heartbeat sleeping its full hour flaps from "live" → "idle"
    → "stale" before its next wake, making the Overview dashboard's
    liveness signal unreliable.
    """
    task_store = TaskStore(Path(os.environ.get("HOMUNCULUS_TASKS_DIR", "./tasks")))
    keepalive_every = 10 * 60.0
    # Wall-clock deadline, not monotonic. time.monotonic() pauses during
    # host suspend (laptop lid close), so a 1-hour sleep can wall-clock
    # into 9 hours and we miss everything overnight. time.time() jumps
    # forward on resume, so the next iteration sees remaining <= 0 and
    # exits — letting tick() run with the actual current time.
    deadline = time.time() + total_seconds
    last_ping = time.time()
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        time.sleep(min(poll_interval, remaining))
        # After host resume, time.time() can jump by hours in a single
        # sleep iteration. Detect the gap and break loudly so the next
        # tick fires right away rather than continuing to spin keepalives.
        now_wall = time.time()
        if now_wall - last_ping >= keepalive_every * 1.5:
            gap = now_wall - last_ping
            print(f"[heartbeat] detected {gap:.0f}s wall-clock gap (host suspend?) — waking", flush=True)
            try:
                events.emit(
                    "host_suspend_detected",
                    name="heartbeat",
                    text=f"gap={gap:.0f}s — host likely slept; firing tick",
                )
            except Exception:
                pass
            break
        if now_wall - last_ping >= keepalive_every:
            try:
                events.emit("service_ping", name="heartbeat", text="alive")
            except Exception:
                pass
            last_ping = now_wall
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
    scheduled = memory.next_tick.pop()
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
    # `scheduled` is a naive ISO string written in the user's TZ — compare
    # in the same frame so a 9-AM-IST schedule doesn't fire 5.5 hours late
    # on a container running in UTC.
    delta = (target - _now_user_naive()).total_seconds()
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
