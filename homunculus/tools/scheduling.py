"""Scheduling tools: schedule_next_tick (one-shot) and structured tasks."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from homunculus.tasks import (
    TaskStore,
    append_scratchpad,
    clear_scratchpad,
    read_scratchpad,
    task_health_summary as _task_health_summary,
    write_scratchpad,
)
from homunculus.user_tz import now_user_naive

from ._state import get_memory


def _task_store() -> TaskStore:
    root = Path(os.environ.get("HOMUNCULUS_TASKS_DIR", "./tasks"))
    return TaskStore(root)


def schedule_next_tick(iso_datetime: str) -> str:
    """One-shot heartbeat wake timer. Validates the input before persisting."""
    mem = get_memory()
    if mem is None:
        return "ERROR: memory subsystem is not initialized"
    try:
        target = datetime.fromisoformat(iso_datetime)
    except ValueError:
        return (
            f"ERROR: '{iso_datetime}' is not a valid ISO 8601 datetime. "
            f"Format: YYYY-MM-DDTHH:MM:SS (e.g. 2026-05-18T08:00:00)."
        )
    # Normalize everything to USER-naive wall clock — the frame the
    # heartbeat's _compute_sleep compares in and the frame the agent's
    # prompts quote. The old datetime.now() was container-naive (UTC in
    # Docker), so a valid "wake me at 13:00 IST" was either rejected as
    # past or silently discarded by _compute_sleep later.
    from homunculus.user_tz import get_user_tz
    if target.tzinfo is not None:
        tz = get_user_tz()
        target = (target.astimezone(tz) if tz else target.astimezone()).replace(tzinfo=None)
    now = now_user_naive()
    if target <= now:
        return f"ERROR: target time {target} is in the past (now: {now})."
    if target > now + timedelta(hours=24):
        return (
            f"ERROR: target time {target} is more than 24h away. "
            f"Schedule something sooner; you can always re-schedule from "
            f"the next tick."
        )
    mem.next_tick.set(target.isoformat(timespec="seconds"))
    delta = target - now
    return f"Scheduled next heartbeat for {target.isoformat(timespec='seconds')} (in {delta})."


def _slug(title: str) -> str:
    """Normalize a title to a comparable slug."""
    import re
    return re.sub(r"[^a-z0-9]+", "", title.lower())


def create_task(
    title: str,
    description: str = "",
    due_at: str | None = None,
    recurrence: str = "none",
    notify: bool = False,
) -> str:
    # Intake clarifier (robustness plan item 7): refuse to save tasks that
    # are too vague to fire. The agent receives the NEEDS_CLARIFICATION
    # marker as the tool result and is expected to relay the question to
    # the user via the chat reply.
    from ._intake import needs_clarification
    if (clarif := needs_clarification(title, description, due_at, recurrence)) is not None:
        return clarif
    store = _task_store()
    # Dedup guard: if a task with a very similar title already exists,
    # update it instead of creating a duplicate. This catches cases where
    # the LLM rephrases the user's intent ("remind me to X" vs "X task").
    title_slug = _slug(title)
    for existing in store.list("all"):
        if _slug(existing["title"]) == title_slug:
            task = store.schedule(existing["id"], due_at or existing.get("due_at") or "", recurrence if recurrence != "none" else existing.get("recurrence", "none"))
            return f"Updated existing task {task['id']} (was {existing['status']}): {task['title']} — due {task.get('due_at')}, recurrence: {task.get('recurrence')}"
    task = store.create(title, description, due_at, recurrence, notify)
    return f"Created task {task['id']}: {task['title']}"


_COMMITMENT_KINDS = {"deadline_check", "event_check_in", "open_loop", "care_check_in"}


def record_commitment(what: str, check_at: str, kind: str = "open_loop") -> str:
    """Record a commitment the agent NOTICED (not user-requested) as a check-in.

    Stored as an inferred, notifying task so it fires through the normal heartbeat
    — this is how the agent follows up proactively on a deadline the user
    mentioned, a promise it made, or an open loop, without being asked. `kind` is
    one of deadline_check / event_check_in / open_loop / care_check_in.
    """
    what = (what or "").strip()
    check_at = (check_at or "").strip()
    if not what:
        return "ERROR: 'what' is required — describe the commitment to follow up on."
    if not check_at:
        return "ERROR: 'check_at' is required — a commitment with no check time can't fire."
    if kind not in _COMMITMENT_KINDS:
        return f"ERROR: kind must be one of {sorted(_COMMITMENT_KINDS)}, got '{kind}'."
    store = _task_store()
    slug = _slug(what)
    for existing in store.list("all"):
        if _slug(existing["title"]) == slug:
            store.schedule(existing["id"], check_at or existing.get("due_at") or "", "none")
            return f"Updated existing commitment {existing['id']}: {existing['title']}"
    desc = f"[commitment:{kind}] Proactive check-in the agent inferred, not a user reminder. {what}"
    task = store.create(what, desc, check_at, "none", notify=True, source="inferred")
    return f"Recorded commitment {task['id']} ({kind}): {task['title']} — check at {task.get('due_at')}"


def task_health_summary() -> str:
    """Pre-computed brief snapshot — JSON wrapper around the pure-data
    helper in tasks.py. See tasks.task_health_summary for the schema."""
    return json.dumps(_task_health_summary(_task_store()), indent=2)


def list_tasks(status: str = "active") -> str:
    tasks = _task_store().list(status)
    if not tasks:
        return f"No {status} tasks."
    lines = []
    for task in tasks:
        due = task.get("due_at") or "no due date"
        recurrence = task.get("recurrence", "none")
        notify = " notify" if task.get("notify") else ""
        lines.append(
            f"- {task['id']} [{task['status']}] {task['title']} "
            f"(due: {due}, recurrence: {recurrence}{notify})"
        )
    return "\n".join(lines)


def complete_task(task_id: str, result: str = "") -> str:
    store = _task_store()
    task = store.complete(task_id, result)
    # Scratchpad is per-attempt working state; a delivered task no
    # longer needs it and the next recurring run should start clean.
    clear_scratchpad(store.root, task_id)
    if task.get("status") == "active":
        return f"Completed recurring task {task_id}; next due {task.get('due_at')}"
    return f"Completed task {task_id}"


def cancel_task(task_id: str, reason: str = "") -> str:
    store = _task_store()
    store.cancel(task_id, reason)
    clear_scratchpad(store.root, task_id)
    return f"Cancelled task {task_id}"


def record_failure(task_id: str, reason: str = "") -> str:
    """Log a failed attempt at a task (genuinely couldn't deliver).

    The heartbeat prompt, AGENTS.md and the TaskGuard all instruct the
    agent to call this — but it was never registered as a tool, so every
    failure attempt errored with 'tool does not exist'. This wraps the
    store method the post-tick code already uses (TaskStore.record_failure):
    logs the run, advances a recurring task's due_at, and auto-cancels
    after the consecutive-failure limit.
    """
    store = _task_store()
    if not (store.get(task_id)):
        return f"ERROR: no task '{task_id}' to record a failure against."
    task = store.record_failure(task_id, reason or "agent reported failure")
    clear_scratchpad(store.root, task_id)
    if task.get("status") == "cancelled":
        return f"Recorded failure for {task_id}; auto-cancelled after repeated failures."
    return f"Recorded failure for {task_id}: {reason[:160]}"


def continue_task(
    task_id: str,
    reason: str = "",
    scratchpad_update: str | None = None,
) -> str:
    """Mark the current run as PARTIAL and save state for the next tick.

    Use this when the task is making real progress but you've hit a
    limit (provider throttling, iteration cap, big-payload context
    pressure) and won't finish *this* tick. The task gets rescheduled
    to ~10 min from now, your scratchpad survives, and the next run
    can read it via the standard task prompt.

    Calling this is strictly better than running out the loop silently:
    the harness records a "partial" run (no failure counter increment),
    and the user does not get a failure notification.

    `scratchpad_update` is optional. If provided it is APPENDED to the
    scratchpad — pass a short summary of what you've done so the next
    run can pick up cleanly ("Fetched problem statement; solution
    pending"). Use write_file directly if you want full control.
    """
    store = _task_store()
    if scratchpad_update:
        append_scratchpad(store.root, task_id, scratchpad_update)
    task = store.mark_partial(task_id, reason or "agent requested continuation")
    partials = task.get("consecutive_partials", 0)
    if partials == 0 and task.get("consecutive_failures", 0) > 0:
        # mark_partial reset partials → escalated to a real failure.
        return (
            f"continue_task: too many consecutive partials — escalated "
            f"task {task_id} to a real failure. The user will be notified."
        )
    return (
        f"continue_task: task {task_id} marked partial (#{partials}); "
        f"next attempt due at {task.get('due_at')}."
    )


def task_scratchpad(task_id: str, content: str | None = None) -> str:
    """Read or overwrite a task's scratchpad.

    With `content=None` (default): returns the current scratchpad
    content for the task, or an empty string if none exists.
    With a non-None `content`: REPLACES the scratchpad and returns
    a short confirmation. To append instead of replace, prefer
    continue_task(scratchpad_update=...).

    Scratchpads survive across attempts and are cleared automatically
    when complete_task succeeds.
    """
    store = _task_store()
    if content is None:
        existing = read_scratchpad(store.root, task_id)
        if not existing:
            return f"Scratchpad for {task_id} is empty."
        return existing
    write_scratchpad(store.root, task_id, content)
    return f"Scratchpad for {task_id} updated ({len(content):,} chars)."


def schedule_task(
    task_id: str,
    due_at: str,
    recurrence: str | None = None,
) -> str:
    task = _task_store().schedule(task_id, due_at, recurrence)
    return (
        f"Scheduled task {task_id} for {task.get('due_at')} "
        f"(recurrence: {task.get('recurrence', 'none')})"
    )


