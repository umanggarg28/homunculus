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

import logging
import os
import sys
import time
from datetime import datetime, timedelta, UTC
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from homunculus import agent_controls, REPO_ROOT
from homunculus import events
from homunculus import tools
from homunculus.core import Agent, measure_llm_usage_since
from homunculus.failures import is_transient_failure
from homunculus.memory import Memory
from homunculus.skills import current_skill_version
from homunculus.tasks import TaskStore, clear_scratchpad
from homunculus.tools.notify import deliver
from homunculus.logging_config import configure_logging

log = logging.getLogger(__name__)


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

━━ STEP 1 — Skill review (MANDATORY, do this first) ━━

Here are your most recent skill-backed deliveries — already extracted for
you. Do NOT read tasks.json; everything you need to judge is right here:

{recent_deliveries}

Skill files live in the memory directory: read_file("memory/skill_<name>.md").
There is NO "skills/" directory — that path always fails.

Before proposing anything, call list_proposals(status="pending") and skip
any skill that already has a pending proposal — never file a duplicate.

Also check the `tool trace` on each delivery. If a tool ran multiple times in
one run (e.g. `notify ×4`), or the trace shows a tool being retried, the skill's
handling of that tool's RESULT is probably stale — read_file the skill and the
tool's current behavior, then reconcile the skill to what the tool actually
returns now (surgical edit). This is how you keep skills in sync with tools that
changed underneath them — the trace is the evidence, don't wait to be told.

For EACH delivery above:
  a) `last run: failure` → read_file the skill, identify what broke from the
     result, and propose_skill(name=<skill>, kind="skill_edit",
     edits=[{"old": "<exact text to change>", "new": "<replacement>"}],
     rationale=<what failed + the fix>). Use `edits` (surgical str_replace),
     not a full body — it changes only what you target and leaves the rest
     verbatim. Copy each `old` from the skill exactly.
  b) `last run: success` → SELF-CRITIQUE the delivered_text. A run can pass
     its success_criteria and still be poor. Judge it honestly against what
     the skill is FOR, and flag:
       - fabricated or placeholder identifiers — made-up/truncated IDs,
         example.com, `item?id=1`, links that clearly aren't real;
       - thin, empty, repetitive, or off-topic content;
       - anything a human reader would call low-quality or wrong even though
         it technically passed.
     If the delivery is genuinely good → no action. If NOT → read_file the
     skill, find the ROOT cause (e.g. a brittle data source the model has to
     guess from), and propose_skill(kind="skill_edit", edits=[{"old": ...,
     "new": ...}]): fix the procedure (prefer a reliable structured data
     source over scraping/guessing — you MAY web_search to find one) AND,
     where it helps, add a stricter success_criterion so the same gap FAILS
     the gate next time. Do NOT fabricate the fix — base it on what you
     actually verify.

propose_skill files the edit for the operator to approve — it does NOT
change the skill now, and you must NOT write_file a skill directly. After
proposing, the skill stays on its current body until approved.

━━ STEP 2 — Learn from yesterday's log ━━

read_file("memory/logs/{yesterday_path}.md")
If the file doesn't exist, skip to Step 3.

Look for:
- User corrections → save as "feedback" memory
- Ongoing goals or project state → save as "project" memory
- Confirmed good choices → save as "feedback" memory
- Commitments — a deadline or event the user mentioned (interview, trip,
  submission) or a promise YOU made that's still open — that ISN'T already a
  task → record_commitment(what, event_at=<when it happens / is due>, kind) so
  you can follow up proactively. At most 2. Only REAL commitments from the log;
  skip anything already tracked. Don't invent them.

━━ STEP 3 — Save memories + hygiene ━━

Save AT MOST 2 new memories. Use remember() with the SAME name to update existing ones — no duplicates.
Always pass all four arguments, exactly these names:
  remember(name="...", description="one line", type="feedback", body="the full content")
Never put a date in a memory name — a dated name creates a new entry every day
(a diary the index drags into every prompt). Reuse one stable name (e.g.
"daily_log_summary") so today's save OVERWRITES yesterday's.
When a memory relates to one you already have, reference it inline as [[its-name]] in the body — cross-links are how recall finds context later.
Call forget() AT MOST 2 times for stale or contradicted memories.

━━ STEP 4 — Reply ━━

One line: what you learned or changed. If you updated a skill, say which one and why.

Rules: no notify(), no shell_exec, no writing workspace files, no reading logs other than yesterday's.
"""


# TaskGuard (the delivery guard for scheduled runs) lives in
# homunculus/task_guard.py; re-exported here (PEP 484 `x as x`) because the
# heartbeat is its primary owner and existing imports/tests reach it via
# heartbeat.TaskGuard:
from homunculus.task_guard import (  # noqa: E402
    TaskGuard as TaskGuard,
    _FAILURE_SENTINELS as _FAILURE_SENTINELS,
    _extract_urls as _extract_urls,
)

# User TZ is autodetected from the browser (see user_tz module) — no env
# var, no hardcoding. The browser writes workspace/user_tz.txt on its first
# visit; this module reads from there and falls back to system local.
from homunculus.user_tz import now_user_tz as _now_user_tz, now_user_naive as _now_user_naive  # noqa: E402


def _today_str() -> str:
    return _now_user_tz().strftime("%Y-%m-%d")


def _yesterday_iso_and_path() -> tuple[str, str]:
    """Return (YYYY-MM-DD, YYYY/MM/YYYY-MM-DD) for yesterday in user TZ."""
    y = _now_user_tz() - timedelta(days=1)
    iso = y.strftime("%Y-%m-%d")
    path_form = y.strftime("%Y/%m/%Y-%m-%d")
    return iso, path_form


# Tools the daily reflection tick must never call. The reflection prompt states
# these rules ("no notify(), no shell_exec, no writing workspace files"), but a
# weak model ignores prose — a reflection tick once fired a false "Updated skill
# X" notification straight to the user's phone because notify() ran unguarded.
# Unlike task ticks, reflection installed no pre-execute hook at all. This guard
# enforces the rules structurally: reflection reviews skills (propose_skill) and
# edits memory (remember/forget), nothing else reaches a side effect.
_REFLECTION_FORBIDDEN = {
    "notify": "Reflection ticks don't message the user. State what you changed "
              "in your final reply instead — the harness surfaces any filed "
              "proposals to the user automatically.",
    "complete_task": "Reflection doesn't run tasks. Skip task-lifecycle calls.",
    "continue_task": "Reflection doesn't run tasks. Skip task-lifecycle calls.",
    "record_failure": "Reflection doesn't run tasks. Skip task-lifecycle calls.",
    "create_task": "Reflection doesn't create reminder tasks — observed misuse: "
                   "status-note tasks like 'reflection-completed-<date>' that "
                   "pollute the store and never fire. For a REAL commitment "
                   "found in the log, use record_commitment.",
    "write_file": "Reflection must not write workspace files. Use propose_skill, "
                  "remember, or forget.",
    "append_file": "Reflection must not write workspace files. Use propose_skill, "
                   "remember, or forget.",
    "shell_exec": "Reflection must not run shell commands.",
}


#: The prompt says "AT MOST 2" for both — stated once, unenforced, so a
#: model can spin rewording the same memory forever, only ever curbed by
#: the generic 3-identical-calls STUCK_LOOP (which restarts every time it
#: rewords). Observed live: 11 remember() calls in one reflection tick, all
#: paraphrases of the same daily summary. The cap makes "at most 2" real.
_REFLECTION_CALL_CAPS = {"remember": 2, "forget": 2}


class _ReflectionToolGuard:
    """The reflection Agent's pre_execute_hook for one tick.

    A fresh instance per tick (constructed in _run_reflection_or_idle) so
    the call counters never leak between days — mirrors how TaskGuard is
    built per task-run rather than kept as module state.
    """

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def __call__(self, name: str, arguments: dict) -> str | None:
        refusal = _REFLECTION_FORBIDDEN.get(name)
        if refusal:
            return f"BLOCKED: {refusal}"
        cap = _REFLECTION_CALL_CAPS.get(name)
        if cap is not None:
            self._counts[name] = self._counts.get(name, 0) + 1
            if self._counts[name] > cap:
                return (
                    f"BLOCKED: {name}() already called {cap} time(s) this "
                    f"reflection tick — that's the limit. Stop here; if the "
                    f"content needs refining, that's a future tick's job."
                )
        return None


#: A task still flagged ``executing`` after this long has lost its agent without
#: calling complete_task/record_failure; clear it so the next tick re-fires it.
_STALE_EXECUTING_SEC = 10 * 60


def _recover_stale_executing_flags(tasks: TaskStore) -> None:
    """Force-clear executing flags older than the stale window.

    main()'s startup cleanup only runs on container restart; if an agent finishes
    without complete_task/record_failure (e.g. a provider-exhaustion limp-along
    that returns a fallback string instead of raising), the flag stays True
    forever and the task is filtered out of due(). Clearing it lets the next tick
    re-fire the task.

    Uses user-local naive now() because ``last_fired_at`` is written by tasks.py
    in the user's wall-clock TZ — mixing container UTC here would treat
    recently-fired tasks as stale on UTC containers in non-UTC zones.
    """
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
        if age > _STALE_EXECUTING_SEC:
            try:
                tasks.record_failure(
                    t["id"],
                    f"executing flag stale ({int(age)}s old) — auto-cleared",
                    increment_failures=False,
                )
                log.info(
                    f"[heartbeat] auto-cleared stale executing flag on {t['id']!r} "
                    f"(age={int(age)}s)",
                )
            except Exception as _e:
                log.error(f"[heartbeat] auto-clear failed for {t['id']}: {_e}")


def _run_reflection_or_idle(
    memory: Memory, model: str | None, tasks: TaskStore, now_iso: str
) -> None:
    """No due tasks this tick: run the once-per-day reflection, or log idle.

    Reflection is gated to once per calendar day (ReflectionStore) and runs under
    a tool guard that blocks notify/shell/writes — it reviews yesterday and may
    file skill proposals, but must never message the user or mutate state (it ran
    unguarded before, and a weak model did message the user from inside it).
    """
    today = _today_str()
    last = memory.reflection.last_date()
    if not (last is None or last < today):
        log.info(f"\n[heartbeat] tick at {now_iso}: no due tasks; skipping LLM")
        return

    # The reflection tool ban rides this Agent, not a process-global hook —
    # a rule stated only in the prompt once let a reflection tick notify()
    # the user a fabricated skill update; run-scoped enforcement can't leak
    # into (or out of) any other agent in the process.
    agent = Agent(memory=memory, model=model, pre_execute_hook=_ReflectionToolGuard())
    yesterday_iso, yesterday_path = _yesterday_iso_and_path()
    log.info(f"\n[heartbeat] REFLECTION tick at {now_iso} "
             f"(reviewing {yesterday_iso}, model={agent.model})")
    # Substitute by explicit replace, not str.format: the template embeds literal
    # JSON braces in its skill-edit examples (edits=[{"old": ...}]) that
    # str.format would misread as fields.
    prompt = (
        REFLECTION_PROMPT_TEMPLATE
        .replace("{today}", today)
        .replace("{yesterday_path}", yesterday_path)
        .replace("{recent_deliveries}", _format_recent_deliveries(tasks))
    )
    response = agent.chat(prompt, source="heartbeat")
    memory.reflection.mark(today)
    log.info(f"[agent] {response}")


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
        log.warning("[heartbeat] HALTED by operator kill switch — tick skipped")
        return

    # Use user-TZ-aware now for the prompt — the agent quotes this back to
    # the user, so naive UTC here causes "the current time is 06:30 IST"
    # mismatches in chat replies.
    now_iso = _now_user_tz().isoformat(timespec="seconds")
    tasks = TaskStore(Path(os.environ.get("HOMUNCULUS_TASKS_DIR", "./tasks")))

    # Recover stale executing flags before computing due tasks, or a task that
    # lost its agent mid-run would stay flagged and never re-fire.
    _recover_stale_executing_flags(tasks)

    due_tasks = tasks.due()
    events.emit("service_ping", name="heartbeat", text="alive")

    # No due tasks → daily reflection (once/day) or idle, then we're done.
    # Due tasks take priority over reflection so a new day's first tick can't
    # starve an overdue task.
    if not due_tasks:
        _run_reflection_or_idle(memory, model, tasks, now_iso)
        return

    # A readable-but-EMPTY tool registry means the MCP tool server is down
    # (import crash, bad dependency) — every task run is guaranteed to fail
    # while still burning LLM calls. Observed live: a rebuild pulled a
    # breaking mcp release, the registry stayed empty for three days, and
    # 30 straight task runs flailed against "Available tools: ." with the
    # user seeing only per-task escalation spam. Distinct from an
    # UNREADABLE registry (introspection hiccup → capability gate fails
    # open, tasks still run). Leave the tasks due — they fire as soon as
    # the registry recovers — and tell the user ONCE per outage.
    if _tool_registry_empty():
        log.error(
            "[heartbeat] tool registry is EMPTY (MCP server down?) — "
            f"skipping {len(due_tasks)} due task(s) until tools return",
        )
        events.emit(
            "tool_registry_empty",
            name="heartbeat",
            text=f"skipping {len(due_tasks)} due task(s) — no tools registered",
        )
        _alert_tool_registry_down_once()
        return
    # Healthy registry → arm the alert for the next outage.
    global _registry_alert_sent
    _registry_alert_sent = False

    log.info(
        f"\n[heartbeat] tick at {now_iso}: {len(due_tasks)} due task(s) "
        f"(model={model or 'default'})",
    )

    memory_root = Path(os.environ.get("HOMUNCULUS_MEMORY_DIR", "./memory"))
    # Run each due task in its OWN isolated agent loop — a fresh Agent, a
    # fresh TaskGuard, its own prompt and success_criteria. Multiplexing
    # several tasks into one shared loop let the weak model cross-contaminate
    # them (deliver one task, then record_failure it while handling another).
    # One scheduled job = one isolated agent context — the pattern OpenClaw
    # (src/cron/isolated-agent) and Letta (EphemeralAgent) both follow.
    for idx, task in enumerate(due_tasks, start=1):
        try:
            _run_task_isolated(
                memory, model, tasks, task, memory_root, now_iso, idx, len(due_tasks)
            )
        except Exception as e:
            # Network-class trouble: propagate so main() does its short 60s
            # retry of the remaining due tasks (completed ones already
            # advanced due_at and won't re-run). A real per-task failure was
            # already recorded inside the helper — isolate it so it never
            # aborts the sibling tasks.
            if _is_transient_network_error(e):
                raise
            log.error(
                f"[heartbeat] task {task.get('id')!r} failed; "
                f"continuing with remaining due task(s): {e}",
            )


def _run_task_isolated(
    memory: Memory,
    model: str | None,
    tasks: TaskStore,
    task: dict[str, Any],
    memory_root: Path,
    now_iso: str,
    task_index: int,
    task_total: int,
) -> None:
    """Execute a single due task in its own isolated agent loop.

    Fresh Agent + fresh TaskGuard scoped to this one task's criteria, so a
    task's delivery and lifecycle can never be confused with another's. Any
    failure is recorded here; transient network errors re-raise so the caller
    can apply its backoff, every other exception is the caller's to isolate.
    """
    prep = prepare_task_run(tasks, task, memory_root, now_iso, forced=False)
    if prep is None:
        return  # planning skipped this task (e.g. its skill file vanished)
    state_sequence, prompt, guard = prep

    # The guard rides the Agent it supervises — run-scoped, so it can never
    # intercept another Agent's tool calls (a chat turn in the same process,
    # a sibling task). The pre-turn hook lets it force a completion message
    # at the last iteration when the task is still unfinished.
    agent = Agent(
        memory=memory,
        model=model,
        pre_execute_hook=guard.on_tool_call,
        post_execute_hook=guard.observe_tool_result,
        pre_turn_hook=guard.on_pre_turn,
    )
    if task_total > 1:
        log.info(
            f"[heartbeat] task {task_index}/{task_total}: {task.get('id')!r} "
            f"(isolated loop, model={agent.model})",
        )

    # Snapshot due_at so we can detect whether complete_task advanced it.
    due_at_before = task.get("due_at")

    started = datetime.now()
    # Wall-clock UTC for events.jsonl scan; events log timestamps are UTC.
    started_utc = datetime.now(UTC)
    # Stamp this task's id on every event the loop emits. Windowing the log by
    # time alone attributes whatever else the heartbeat was doing to this run.
    try:
        with events.task_context(str(task.get("id") or "")):
            response = agent.chat(
                prompt,
                source="heartbeat",
                state_sequence=state_sequence,
                expected_completions=1,
            )
    except Exception as e:
        # Record the outcome (infra → partial, real error → failure), then
        # re-raise so main() can apply its transient-network backoff. Shared
        # with run-now via settle_task_failure so the two can't diverge.
        settle_task_failure(
            memory,
            tasks,
            task,
            guard,
            f"{type(e).__name__}: {e}",
            due_at_before=due_at_before,
            duration_s=(datetime.now() - started).total_seconds(),
            started_utc=started_utc,
        )
        raise
    log.info(f"[agent] {response}")

    # Single deterministic settlement — the same close-out run-now uses.
    settle_task_outcome(
        memory,
        tasks,
        task,
        guard,
        due_at_before=due_at_before,
        started=started,
        started_utc=started_utc,
    )


# ── Shared task-execution core ───────────────────────────────────────────
#
# One implementation of how a task's run is guarded and settled, called by
# BOTH the scheduled tick (_run_task_isolated) and the manual run-now
# (web_api.tasks_run_stream). The two used to hand-copy this logic and drift
# apart (delivery-key ledger, auto-complete, skill rating, …); centralizing
# it is the same "one core, many thin entry points" shape Letta uses
# (step()/step_stream() both delegate to a single _step) and OpenClaw uses
# (cron + chat both call one agent-core). Streaming vs. blocking and the
# escalation-notify policy are the only legitimate differences, expressed as
# the caller's choice / a parameter — never a second copy.


def prepare_task_run(
    tasks: TaskStore,
    task: dict[str, Any],
    memory_root: Path,
    now_iso: str,
    *,
    forced: bool,
) -> tuple[list[dict] | None, str, TaskGuard] | None:
    """Plan + stamp-fired + build prompt and guard for one task — identical
    prep for the scheduled tick and the manual run-now.

    Runs _plan_tick (which folds the skill's success_criteria onto the task,
    applies the capability gate, injects the playbook, and yields any state
    sequence), stamps last_fired_at, renders the heartbeat prompt, and builds
    the guard. `forced=True` adds the "run now regardless of due_at" note for
    an operator-triggered run. Returns (state_sequence, prompt, guard), or
    None if planning skipped the task (e.g. its skill file vanished mid-run).
    """
    state_sequence, selected_tasks, playbooks = _plan_tick([task], memory_root)
    if not selected_tasks:
        return None

    # Stamp `last_fired_at` only on the task we actually attempt.
    for t in selected_tasks:
        try:
            tasks.mark_fired(t["id"])
        except Exception as e:
            log.error(f"[heartbeat] mark_fired failed for {t['id']}: {e}")

    due_tasks_block = _format_due_tasks(selected_tasks, forced=forced)
    if playbooks:
        due_tasks_block += "\n\n" + "\n\n".join(playbooks)
    prompt = HEARTBEAT_PROMPT_TEMPLATE.format(now_iso=now_iso, due_tasks=due_tasks_block)
    return state_sequence, prompt, build_task_guard(task)


def build_task_guard(task: dict[str, Any]) -> TaskGuard:
    """A TaskGuard scoped to one task: its success_criteria, the
    delivered-key ledger that notify_unique consults, and the skill's
    requires_tools list that gates complete_task. Call AFTER _plan_tick
    has folded the skill's criteria and required tools onto the task."""
    tid = task["id"]
    return TaskGuard(
        {tid: task.get("success_criteria") or []},
        delivered_by_task={tid: {d.get("key", "") for d in (task.get("delivered") or [])}},
        required_calls_by_task={tid: task.get("required_tool_calls") or []},
    )


def settle_task_failure(
    memory: Memory,
    tasks: TaskStore,
    task: dict[str, Any],
    guard: TaskGuard,
    err: str,
    *,
    due_at_before: str | None,
    duration_s: float,
    started_utc: datetime,
) -> None:
    """Record the outcome when a task's agent loop RAISED.

    Notifies that went out before the crash are ground truth → record their
    delivery keys first. A loop that raised after complete_task already
    advanced due_at is left alone (the delivery happened). Infrastructure
    trouble (provider/network) is a partial — retry in ~10 min, no
    consecutive_failures bump, due_at unchanged; a real error is a recorded
    failure. Control flow after recording (re-raise for backoff vs. stream an
    error) stays with the caller.
    """
    task_id = task["id"]
    _record_delivery_keys(tasks, guard, [task])
    try:
        current = tasks.get(task_id)
        if current and current.get("due_at") != due_at_before:
            log.info(
                f"[heartbeat] {task_id} due_at advanced — task was completed; "
                f"skipping record_failure",
            )
            return
        usage = _stamp_skill_version(measure_llm_usage_since(started_utc), task, memory)
        if _is_infra_error(err):
            log.info(
                f"[heartbeat] {task_id} provider-exhaustion — marking partial, "
                f"will retry shortly",
            )
            tasks.mark_partial(task_id, err, duration_s=duration_s, usage=usage)
            events.emit(
                "task_partial",
                name=task_id,
                text=events.truncate_preview(err),
                result="provider_exhaustion · retry in ~10 min",
            )
        else:
            updated = tasks.record_failure(task_id, err, duration_s=duration_s, usage=usage)
            events.emit(
                "task_failure",
                name=task_id,
                text=events.truncate_preview(err),
                result=(
                    f"consecutive_failures={updated.get('consecutive_failures', '?')} "
                    f"status={updated.get('status', '?')}"
                ),
            )
    except Exception as inner:
        log.error(f"[heartbeat] settle_task_failure failed for {task_id}: {inner}")


def _stamp_skill_version(usage: dict[str, Any], task: dict[str, Any], memory: Memory) -> dict[str, Any]:
    """Record which version of the task's skill produced this run.

    The same move as recording `model`: an outcome is only comparable if you
    know what produced it. Without this a scorecard blends the runs before a
    skill edit with the runs after it, and the edit's effect is invisible —
    which is how a skill reached version 12 with no evidence that any edit
    helped. Tasks with no skill are left alone.
    """
    skill = task.get("skill")
    if skill:
        version = current_skill_version(memory.root, str(skill))
        if version:
            usage = {**usage, "skill_version": version}
    return usage


def settle_task_outcome(
    memory: Memory,
    tasks: TaskStore,
    task: dict[str, Any],
    guard: TaskGuard,
    *,
    due_at_before: str | None,
    started: datetime,
    started_utc: datetime,
    fire_escalation_notify: bool = True,
) -> None:
    """Deterministic close-out after a task's agent loop RETURNS normally.

    Decides from the machine-checked criteria the guard tracked:
      - delivered (due_at advanced) → success: attribute usage/text/trace,
        settle quiz, rate skill success.
      - agent explicitly closed it (record_failure/cancel/continue) → respect
        that; rate skill failure only if the recorded run failed.
      - silent drop → _settle_silent_drop, which auto-completes when every
        criterion passed (agent merely skipped complete_task) or marks partial.

    Sent notifies are recorded as delivered regardless of how the lifecycle
    ended, so no later run re-sends the same content. `fire_escalation_notify`
    is False for run-now (operator is watching the stream).
    """
    task_id = task["id"]
    silently_dropped = task_id in set(guard.expected_remaining())
    _record_delivery_keys(tasks, guard, [task])
    usage = _stamp_skill_version(measure_llm_usage_since(started_utc), task, memory)
    try:
        current = tasks.get(task_id)
        if current is None:
            return
        if current.get("due_at") != due_at_before:
            # complete_task ran, due_at advanced. Retrofit usage and the
            # delivered text onto the success run the tool layer appended
            # (the latter feeds the reflection's quality self-critique).
            tasks.attribute_usage_to_last_run(task_id, usage)
            tasks.attribute_delivered_text_to_last_run(task_id, guard.combined_notify_text())
            tasks.attribute_tool_trace_to_last_run(task_id, guard.tool_trace())
            _settle_quiz_pending(task, delivered=True)
            _rate_task_skill(memory, task, "success")
            return
        if not silently_dropped:
            # Agent explicitly closed the task (record_failure / cancel_task /
            # continue_task) — respect its decision. A non-delivered run drops
            # any quiz pending so the CHAT badge never lights for a question
            # the user never received. Rate the skill a failure only when the
            # recorded run is an actual failure (not a cancel / deferral).
            _settle_quiz_pending(task, delivered=False)
            last_runs = current.get("last_runs") or []
            if last_runs and last_runs[-1].get("status") == "failure":
                tasks.attribute_tool_trace_to_last_run(task_id, guard.tool_trace())
                _rate_task_skill(memory, task, "failure")
            return
        _settle_silent_drop(
            tasks,
            task,
            guard,
            duration_s=(datetime.now() - started).total_seconds(),
            usage=usage,
            fire_escalation_notify=fire_escalation_notify,
        )
        _settle_quiz_pending(task, delivered=False)
    except Exception as inner:
        log.error(f"[heartbeat] settle_task_outcome failed for {task_id}: {inner}")


def _tool_registry_empty() -> bool:
    """True when the tool catalogue is readable and has ZERO entries.

    Empty and unreadable are opposite signals: unreadable → introspection
    hiccup, fail open; empty → the MCP tool server never started, nothing
    can succeed, fail LOUD (tick() skips task runs and alerts once).
    """
    try:
        return not (getattr(tools, "SCHEMAS", None) or [])
    except Exception:
        return False


#: One alert per outage, not one per tick: reset only when a tick sees a
#: healthy registry again.
_registry_alert_sent = False


def _alert_tool_registry_down_once() -> None:
    global _registry_alert_sent
    if _registry_alert_sent:
        return
    _registry_alert_sent = True
    try:
        from homunculus.tools.notify import deliver
        deliver(
            "🛑 My tool server failed to start, so scheduled tasks are on "
            "hold (they'll resume automatically once it's back). This "
            "needs an operator look: `docker logs homunculus-heartbeat-1 "
            "| grep -i mcp`."
        )
    except Exception as e:  # noqa: BLE001 — alerting must never crash the tick
        log.info(f"[heartbeat] registry-down alert failed: {e}")


def _known_tool_names() -> set[str] | None:
    """Live registered tool names from the tool catalogue, or None if it
    can't be read (then the capability gate is skipped — fail open rather
    than block every task on an introspection hiccup)."""
    try:
        names: set[str] = set()
        for s in getattr(tools, "SCHEMAS", []) or []:
            fn = s.get("function") if isinstance(s, dict) else None
            name = (fn or {}).get("name") if fn else (s.get("name") if isinstance(s, dict) else None)
            if name:
                names.add(name)
        return names or None
    except Exception:
        return None


def _plan_tick(
    due_tasks: list[dict[str, Any]],
    memory_root: Path,
) -> tuple[list[dict] | None, list[dict[str, Any]], list[str]]:
    """Plan one task's run: its state sequence and the playbook it gets.

    Returns (state_sequence, selected_tasks, playbook_blocks). The caller
    (_run_task_isolated) passes a single-task list, so each due task is
    planned and executed in its own isolated agent loop; tasks never share
    a loop. The signature stays list-shaped to keep the per-task selection
    logic (capability gate, criteria folding) in one place.

    A task linked to a skill with a `states:` frontmatter declaration pins
    the agent's turns 0..N-1 (state_sequence) so the fixed tool order is
    enforced; with one task per loop there is nothing else to confuse it.

    Stateless skills contribute their playbook body to the prompt.
    If the body were injected ONLY for state-machine skills, a task linked
    to an ordinary skill would run with no playbook at all, so the model
    would improvise from web_search (e.g. an algomap.io link) instead of
    following its own instructions (e.g. a playbook that says LeetCode
    GraphQL only).
    """
    from homunculus.skills import (
        effective_success_criteria,
        load_skill_playbook,
        load_skill_requires_tools,
    )

    known_tools = _known_tool_names()

    playbooks: list[str] = []
    for t in due_tasks:
        skill_name = t.get("skill")
        if not skill_name:
            continue
        try:
            states, body = load_skill_playbook(memory_root, skill_name)
        except FileNotFoundError:
            log.info(
                f"[heartbeat] {t['id']!r} skill {skill_name!r} not found; "
                f"falling back to free-form",
            )
            continue
        # Capability gate (Hermes requires_tools): if the skill depends on tools
        # that aren't registered, do NOT inject its playbook — that's the path
        # that made the model fabricate (the morning-brief weather/calendar).
        # Inject a blocker directive instead so the agent records a clean
        # failure rather than improvising a capability it doesn't have.
        required_tools = load_skill_requires_tools(memory_root, skill_name)
        if known_tools is not None:
            missing = [tn for tn in required_tools if tn not in known_tools]
            if missing:
                log.info(
                    f"[heartbeat] {t['id']!r} skill {skill_name!r} requires "
                    f"unavailable tools {missing}; blocking (no fabrication)",
                )
                events.emit(
                    "skill_capability_missing",
                    name=t["id"],
                    text=f"{skill_name} requires {missing}",
                    result="blocked — record_failure",
                )
                playbooks.append(
                    f"# Task '{t['id']}' BLOCKED — missing capability\n\n"
                    f"The skill {skill_name!r} requires tool(s) that are not "
                    f"available right now: {missing}. You CANNOT complete this "
                    f"task and you must NOT improvise or fabricate the missing "
                    f"data. Call record_failure(task_id='{t['id']}', "
                    f"reason='skill requires unavailable tool(s): {missing}') "
                    f"and do nothing else for this task."
                )
                continue
        # Skill is the source of truth for its own quality bar: fold any
        # success_criteria it declares into the task's effective criteria
        # (additive — never weakens the task). Both the prompt
        # (_format_due_tasks) and the TaskGuard read t["success_criteria"],
        # so setting it on this ephemeral, freshly-loaded task dict covers
        # both without persisting anything back to tasks.json.
        t["success_criteria"] = effective_success_criteria(t, memory_root)
        # requires_tools also folds onto the ephemeral task: the existence
        # gate above proved the tools are registered; the TaskGuard uses
        # this list to enforce that they were actually CALLED before the
        # task may complete (build_task_guard → required_calls_by_task).
        t["required_tool_calls"] = required_tools
        block = (
            f"# Playbook for task '{t['id']}' "
            f"(auto-loaded from {skill_name})\n\n{body.strip()}"
        )
        if states:
            log.info(
                f"[heartbeat] {t['id']!r} → state machine ({len(states)} states) "
                f"from {skill_name!r}",
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
            log.error(f"[heartbeat] record_delivery failed for {task['id']}: {e}")


# The quiz coach is the one skill that arms a "your turn" pending. The harness
# owns the delivery verdict, so it (not the weak model) decides whether the
# pending is real — confirm on a delivered run, drop it on a failed one.
_QUIZ_COACH_SKILL = "skill_quiz_coach"


def _settle_quiz_pending(task: dict[str, Any], *, delivered: bool) -> None:
    """Reconcile the quiz pending with the run's objective outcome. No-op for
    non-quiz tasks. delivered=True → arm the badge/grading; False → clear the
    pending so a failed delivery never leaves a stale CHAT badge."""
    if task.get("skill") != _QUIZ_COACH_SKILL:
        return
    try:
        from homunculus.quiz import _store
        if delivered:
            _store().confirm_delivered()
        else:
            _store().clear_pending()
    except Exception as e:
        log.error(f"[heartbeat] quiz pending settle failed for {task['id']}: {e}")


def _rate_task_skill(memory: "Memory", task: dict[str, Any], outcome: str) -> None:
    """Record a skill's success/failure from the harness's authoritative verdict,
    not the model's self-report. The weak model is unreliable about calling
    rate_skill (and can fabricate it); the harness already knows whether the run
    delivered, so it owns the signal that drives the reflection's skill review.
    No-op for tasks without a linked skill."""
    skill_name = task.get("skill")
    if not skill_name:
        return
    try:
        # rate_skill slugifies and re-prepends `skill_`; strip the prefix so it
        # resolves to skill_<slug>.md directly.
        memory.rate_skill(skill_name.removeprefix("skill_"), outcome)
    except Exception as e:
        log.error(f"[heartbeat] rate_skill({skill_name}, {outcome}) failed: {e}")


def _settle_silent_drop(
    tasks: TaskStore,
    task: dict[str, Any],
    guard: TaskGuard,
    duration_s: float,
    usage: dict[str, Any],
    fire_escalation_notify: bool = True,
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
        # Same attribution the explicit-close path records: without it,
        # every auto-completed run reaches the reflection with no
        # delivered_text/tool_trace — blind on exactly the runs where the
        # model already failed to close cleanly.
        tasks.attribute_delivered_text_to_last_run(task_id, guard.combined_notify_text())
        tasks.attribute_tool_trace_to_last_run(task_id, guard.tool_trace())
        log.info(
            f"[heartbeat] {task_id} auto-completed — criteria satisfied, "
            f"agent omitted complete_task",
        )
        events.emit(
            "task_complete",
            name=task_id,
            text="harness auto-complete: criteria satisfied; agent omitted complete_task",
        )
        return

    log.info(
        f"[heartbeat] {task_id} silently dropped — agent did not call "
        f"complete_task OR record_failure. Recording soft failure to clear "
        f"executing flag.",
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
    tasks.attribute_tool_trace_to_last_run(task_id, guard.tool_trace())
    events.emit(
        "task_partial",
        name=task_id,
        text="silent drop (no termination tool called)",
        result=(
            f"consecutive_partials={updated.get('consecutive_partials', '?')}"
            f" · retry in ~10 min"
        ),
    )
    # A silent drop is recorded as a failure (above) so it surfaces in the
    # next reflection tick, which reads the trace and files a gated
    # propose_skill edit if the skill itself is at fault. Skill content is
    # only ever changed through that human-approved flow — the harness does
    # not edit skill files directly.
    # Autonomous fallback notify — only when mark_partial ESCALATED to
    # a real failure (consecutive_failures > 0 after the call). Plain
    # partials are routine continuation state, not user-actionable, so
    # notifying on each would spam the user every 10 min. Refresh from
    # disk because mark_partial mutated the task.
    #
    # Suppressed for a manual run-now (fire_escalation_notify=False): the
    # operator triggered it and is watching the stream, so an "I tried this
    # multiple times" push would be wrong — that message is for unattended
    # scheduled retries.
    if fire_escalation_notify and task.get("notify"):
        refreshed = tasks.get(task_id) or {}
        escalated = int(refreshed.get("consecutive_failures", 0)) > 0
        if not escalated:
            log.info(
                f"[heartbeat] {task_id} partial — suppressing user "
                f"notification (will only fire on escalation)",
            )
            return
        try:
            title = task.get("title") or task_id
            deliver(
                f"⚠️ I tried '{title}' multiple times today and "
                f"couldn't get it through (provider limits or "
                f"task is broken). Pausing automatic retries; "
                f"check Traces if you want to know why."
            )
        except Exception as notify_err:
            log.info(
                f"[heartbeat] fallback-notify failed for {task_id}: {notify_err}",
            )


def _format_due_tasks(tasks: list[dict], forced: bool = False) -> str:
    import json as _json
    from homunculus.tasks import read_scratchpad
    tasks_root = Path(os.environ.get("HOMUNCULUS_TASKS_DIR", "./tasks"))
    lines = []
    if forced:
        # Manual "run now" (operator clicked ARMED / hit run-stream). The
        # task's due_at is its NEXT scheduled occurrence, which is almost
        # always in the future — without this the weak model reads that
        # future date, concludes "nothing is due", and bails without
        # running the skill. Override that reading.
        lines.append(
            "NOTE: the operator manually triggered the task(s) below RIGHT "
            "NOW. Run them immediately regardless of the scheduled due_at "
            "(which is just the next recurrence) — do NOT skip them as "
            '"not due yet".'
        )
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
            block += "\n  scratchpad (from prior run):\n    " + preview.replace("\n", "\n    ")
        lines.append(block)
    return "\n".join(lines)


def _format_recent_deliveries(tasks: TaskStore, text_cap: int = 1200) -> str:
    """Pre-extract each skill-backed task's most recent run for the daily
    reflection's self-critique.

    The harness owns the mechanical extraction; the model owns only the
    judgment. A clean-room test (2026-06-15) showed the weak model thrashes
    when told to navigate tasks.json itself — it burned its whole turn
    budget re-reading the file and never reached the critique. Handing it
    the delivered text directly (the same pattern as _format_due_tasks /
    week_in_review) turns the critique into a 2-3 turn judgment task.
    """
    blocks: list[str] = []
    for task in tasks.list("all"):
        skill = task.get("skill")
        runs = task.get("last_runs") or []
        if not skill or not runs:
            continue
        last = runs[-1]
        status = last.get("status", "?")
        # Route platform/transient failures away from skill review — they have
        # no skill fix, and handing them to the "diagnose + propose an edit"
        # loop makes a weak model thrash on an unfixable error. Learn only from
        # genuine failures (Reflexion); classify infra as platform-level and
        # route it elsewhere — retry + the dashboard alert (Temporal/LangGraph).
        if status != "success" and is_transient_failure(last.get("result")):
            continue
        header = (
            f"### task: {task.get('id')}  (skill: {skill})\n"
            f"last run: {status}"
        )
        if status == "success":
            delivered = (last.get("delivered_text") or "").strip()
            if not delivered:
                # Older runs predate delivered_text capture — nothing to
                # critique, and the result summary alone isn't the content.
                continue
            if len(delivered) > text_cap:
                delivered = delivered[:text_cap] + " …[truncated]"
            body = "delivered_text (what the user received):\n  " + delivered.replace("\n", "\n  ")
        else:
            body = "result: " + (last.get("result") or "")[:400]
        # The execution trace lets the reflection spot skill staleness that
        # delivered_text/status can't — e.g. a tool fired repeatedly because
        # the skill's handling of its result is out of date.
        trace = (last.get("tool_trace") or "").strip()
        if trace:
            body += "\ntool trace: " + trace
            if "×" in trace:
                body += ("\n  ⚠ a tool ran multiple times in one run — if the skill "
                         "didn't intend that, its handling of that tool's result may be stale.")
        blocks.append(header + "\n" + body)
    if not blocks:
        return "(no recent skill-backed deliveries captured yet)"
    return "\n\n".join(blocks)


def main() -> None:
    configure_logging()
    load_dotenv(REPO_ROOT / ".env")
    if not os.environ.get("HOMUNCULUS_API_KEY"):
        sys.exit("HOMUNCULUS_API_KEY is not set.")

    interval_min = int(os.environ.get("HEARTBEAT_INTERVAL_MINUTES", "60"))
    memory_dir = Path(os.environ.get("HOMUNCULUS_MEMORY_DIR", "./memory"))
    # Heartbeat follows the PRIMARY model unless explicitly overridden via
    # HOMUNCULUS_MODEL_HEARTBEAT (A/B testing knob). The old hardcoded
    # default meant a primary-model swap in .env silently left the daemon
    # on the previous model — one knob must move both loops.
    from homunculus.llm import MODEL as _primary_model
    model = os.environ.get("HOMUNCULUS_MODEL_HEARTBEAT") or _primary_model

    memory = Memory(memory_dir)
    tools.init(memory, autonomous=True)

    dropped = events.rotate(keep_days=14)
    if dropped:
        log.info(f"[heartbeat] rotated _events.jsonl: dropped {dropped} lines older than 14 days")

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
            log.info(f"[heartbeat] cleared stuck executing flag on task {_t['id']!r}")
        except Exception as _e:
            log.error(f"[heartbeat] could not clear executing on {_t['id']}: {_e}")

    log.info(f"[heartbeat] starting, interval = {interval_min} min, model = {model}")

    # Advisory posture audit. Write-time validation only protects what is
    # written after the rule exists; this re-examines what is already stored.
    # Findings are logged, never enforced — see doctor.py.
    try:
        from homunculus.doctor import run_startup_audit
        run_startup_audit(_task_store.all())
    except Exception as _audit_err:
        log.warning(f"[heartbeat] startup audit skipped: {_audit_err}")

    default_interval = interval_min * 60
    tick_failed = False
    while True:
        try:
            tick(memory, model=model)
            tick_failed = False
        except Exception as e:
            # Don't let one bad tick kill the daemon. Log and continue.
            log.exception("[heartbeat] error during tick")
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
        log.info(f"[heartbeat] sleeping {sleep_seconds:.0f}s, next tick ~{wake_at}")
        memory.next_tick.set(wake_at)
        _interruptible_sleep(sleep_seconds)
        memory.next_tick.pop()  # consumed — clear so stale value doesn't persist after waking


def _is_infra_error(err: str) -> bool:
    """True when an agent-loop exception string describes infrastructure
    (LLM provider / network) trouble rather than a broken task.

    Infra errors mark the task PARTIAL (retry ~10 min, scratchpad
    survives); everything else records a real failure (advances a
    recurring task to its next occurrence and counts toward
    auto-cancel). The error string is `f"{type(e).__name__}: {e}"`, so
    the typed exception name is the exact anchor; the message markers
    stay as a fallback for paths that only forward the message text.
    """
    return any(
        marker in err
        for marker in (
            "ProviderExhaustedError",    # llm.py's typed chain-exhaustion error
            "All providers exhausted",   # its message, for message-only paths
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
            log.warning(f"[heartbeat] detected {gap:.0f}s wall-clock gap (host suspend?) — waking")
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
            log.info("[heartbeat] task became due mid-sleep — waking early")
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
        log.warning(f"[heartbeat] could not parse scheduled time '{scheduled}', using default")
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
        log.warning(f"[heartbeat] scheduled time {scheduled} is in the past, using default")
        return min(default_seconds, next_task) if next_task is not None else default_seconds
    # The schedule_next_tick tool already caps at 24h on the way in, but
    # double-check here as a defense-in-depth.
    capped = min(delta, 24 * 3600)
    if capped < delta:
        log.warning(f"[heartbeat] capping {delta:.0f}s schedule to 24h")
    return min(capped, next_task) if next_task is not None else capped


if __name__ == "__main__":
    main()
