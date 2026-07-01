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
import re
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
When a memory relates to one you already have, reference it inline as [[its-name]] in the body — cross-links are how recall finds context later.
Call forget() AT MOST 2 times for stale or contradicted memories.

━━ STEP 4 — Reply ━━

One line: what you learned or changed. If you updated a skill, say which one and why.

Rules: no notify(), no shell_exec, no writing workspace files, no reading logs other than yesterday's.
"""


_URL_RE = re.compile(r"https?://[^\s<>\"'\)\]}]+")
# Trailing punctuation that commonly clings to a URL in prose/markdown but
# isn't part of the address (e.g. "see https://x/a)." → strip ").").
_URL_TRAILING = ".,;:!?)]}>\"'"


def _extract_urls(text: str) -> list[str]:
    """Pull URLs out of free text, trimming trailing punctuation. Order-
    preserving and de-duplicated."""
    seen: dict[str, None] = {}
    for raw in _URL_RE.findall(text or ""):
        url = raw.rstrip(_URL_TRAILING)
        if url:
            seen.setdefault(url, None)
    return list(seen)


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
        # Ordered tool-call trace for the tick — the sequence of tool names the
        # agent invoked. Attributed to the run record so the daily reflection
        # can spot skill staleness that delivered_text/status can't (e.g. a tool
        # called repeatedly because the skill misreads its result).
        self._tool_trace: list[str] = []
        # Concatenated text of every tool RESULT this tick (minus notify's own
        # result). The notify_links_grounded criterion checks delivered URLs
        # against this — a link the agent didn't get from a tool is fabricated.
        self._tool_result_text: list[str] = []

    def on_tool_call(self, name: str, arguments: dict) -> str | None:
        """Hook fn passed to tools.set_pre_execute_hook().

        Returns None to allow the call, or a non-empty string to block it
        and return that string as the tool result.
        """
        self._tool_trace.append(name)
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
                    "BLOCKED: notify() not sent. Criteria failed:\n"
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
            # stops the model from calling complete_task WITHOUT ever
            # calling notify — otherwise a task can close (e.g. "could not
            # fetch problem ...; task marked complete") with the user
            # receiving nothing. The result string must start with
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

        if name == "rate_skill":
            # The harness rates skills from the authoritative run verdict after
            # the tick (success vs record_failure), so a self-reported rating
            # here would double-count or fabricate. Suppress it during ticks.
            return (
                "Noted — skill outcomes for scheduled runs are recorded "
                "automatically from the task result; no rate_skill call needed."
            )

        return None  # all other tools pass through unmodified

    def observe_tool_result(self, name: str, result: str) -> None:
        """Post-execute hook: record what a tool returned this tick.

        Feeds the notify_links_grounded criterion. notify's own result is
        skipped — it's a delivery receipt, not a data source, so it can't be
        used to self-validate a fabricated link.
        """
        if name == "notify" or not result:
            return
        self._tool_result_text.append(result)

    def _grounded_blob(self) -> str:
        """All tool-result text seen this tick, for link-provenance checks."""
        return "\n".join(self._tool_result_text)

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

    def combined_notify_text(self) -> str:
        """The full text the agent sent via notify() this run — what actually
        reached the user. The heartbeat retrofits this onto the run record
        (delivered_text) so the daily reflection can self-critique delivery
        QUALITY, not just pass/fail. Empty if nothing was sent."""
        return "\n\n".join(t for t in self._notify_texts if t)

    def tool_trace(self) -> str:
        """The tick's tool-call sequence as a compact, run-over-counted string,
        e.g. 'quiz_pick, notify ×4, recall, complete_task'. Consecutive repeats
        are collapsed with a count so the reflection sees retry storms at a
        glance. Empty if no tools ran."""
        if not self._tool_trace:
            return ""
        parts: list[tuple[str, int]] = []
        for name in self._tool_trace:
            if parts and parts[-1][0] == name:
                parts[-1] = (name, parts[-1][1] + 1)
            else:
                parts.append((name, 1))
        return ", ".join(n if c == 1 else f"{n} ×{c}" for n, c in parts)

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
        from homunculus.config import get_config  # local import — see docstring
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

            elif ctype == "notify_links_grounded":
                # Every URL in the delivery must appear verbatim in a tool
                # result this run. Catches the fabricated-link failure mode —
                # the model skips the fetch tool and invents placeholder
                # (example.com) or hallucinated links that pass a shape check
                # like notify_matches. We verify against the actual tool
                # output, never the model's narration.
                grounded = self._grounded_blob()
                for url in _extract_urls(combined):
                    if url not in grounded:
                        failures.append(
                            f"notify text contains a link no tool returned this "
                            f"run: {url} — every URL must be pasted verbatim from "
                            f"a tool result (e.g. news_headlines). Do not write, "
                            f"shorten, or invent links."
                        )

            else:
                # Unknown criterion type — skip rather than hard-fail so
                # adding new types doesn't break existing tasks.
                pass

        return failures


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
    "write_file": "Reflection must not write workspace files. Use propose_skill, "
                  "remember, or forget.",
    "append_file": "Reflection must not write workspace files. Use propose_skill, "
                   "remember, or forget.",
    "shell_exec": "Reflection must not run shell commands.",
}


def _reflection_tool_guard(name: str, arguments: dict) -> str | None:
    """Pre-execute hook for the reflection tick: refuse a forbidden tool by
    returning a string (surfaced to the agent as that tool's result), else None
    to allow. Mirrors how task ticks install guard.on_tool_call."""
    refusal = _REFLECTION_FORBIDDEN.get(name)
    return f"BLOCKED: {refusal}" if refusal else None


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

    agent = Agent(memory=memory, model=model)
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
    tools.set_pre_execute_hook(_reflection_tool_guard)
    try:
        response = agent.chat(prompt, source="heartbeat")
    finally:
        tools.set_pre_execute_hook(None)
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
    agent = Agent(memory=memory, model=model)
    if task_total > 1:
        log.info(
            f"[heartbeat] task {task_index}/{task_total}: {task.get('id')!r} "
            f"(isolated loop, model={agent.model})",
        )

    prep = prepare_task_run(tasks, task, memory_root, now_iso, forced=False)
    if prep is None:
        return  # planning skipped this task (e.g. its skill file vanished)
    state_sequence, prompt, guard = prep

    # Snapshot due_at so we can detect whether complete_task advanced it.
    due_at_before = task.get("due_at")

    tools.set_pre_execute_hook(guard.on_tool_call)
    tools.set_post_execute_hook(guard.observe_tool_result)
    # Item 5 (pragmatic slice): install the turn-level hook so the guard
    # can inject a forced completion message at iter MAX_TURNS-1 when the
    # task is still unfinished.
    tools.set_pre_turn_hook(guard.on_pre_turn)

    started = datetime.now()
    # Wall-clock UTC for events.jsonl scan; events log timestamps are UTC.
    started_utc = datetime.now(UTC)
    try:
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
            tasks,
            task,
            guard,
            f"{type(e).__name__}: {e}",
            due_at_before=due_at_before,
            duration_s=(datetime.now() - started).total_seconds(),
            started_utc=started_utc,
        )
        raise
    finally:
        tools.set_pre_execute_hook(None)
        tools.set_post_execute_hook(None)
        tools.set_pre_turn_hook(None)
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
    """A TaskGuard scoped to one task: its success_criteria plus the
    delivered-key ledger that notify_unique consults. Call AFTER _plan_tick
    has folded the skill's criteria onto the task."""
    tid = task["id"]
    return TaskGuard(
        {tid: task.get("success_criteria") or []},
        delivered_by_task={tid: {d.get("key", "") for d in (task.get("delivered") or [])}},
    )


def settle_task_failure(
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
        usage = measure_llm_usage_since(started_utc)
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
    usage = measure_llm_usage_since(started_utc)
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
        if known_tools is not None:
            missing = [tn for tn in load_skill_requires_tools(memory_root, skill_name)
                       if tn not in known_tools]
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
