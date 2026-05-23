"""Scheduling tools: schedule_next_tick (one-shot) and structured tasks."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

from tasks import TaskStore

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
    # Normalize tz-aware → naive local before comparing to datetime.now().
    if target.tzinfo is not None:
        target = target.astimezone().replace(tzinfo=None)
    now = datetime.now()
    if target <= now:
        return f"ERROR: target time {target} is in the past (now: {now})."
    if target > now + timedelta(hours=24):
        return (
            f"ERROR: target time {target} is more than 24h away. "
            f"Schedule something sooner; you can always re-schedule from "
            f"the next tick."
        )
    mem.set_next_tick(target.isoformat(timespec="seconds"))
    delta = target - now
    return f"Scheduled next heartbeat for {target.isoformat(timespec='seconds')} (in {delta})."


def create_task(
    title: str,
    description: str = "",
    due_at: str | None = None,
    recurrence: str = "none",
    notify: bool = False,
) -> str:
    task = _task_store().create(title, description, due_at, recurrence, notify)
    return f"Created task {task['id']}: {task['title']}"


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
    task = _task_store().complete(task_id, result)
    if task.get("status") == "active":
        return f"Completed recurring task {task_id}; next due {task.get('due_at')}"
    return f"Completed task {task_id}"


def cancel_task(task_id: str, reason: str = "") -> str:
    _task_store().cancel(task_id, reason)
    return f"Cancelled task {task_id}"


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


