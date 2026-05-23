"""Structured task state for Homunculus.

Tasks are operational state, not semantic memory. Memory says "what is true";
tasks say "what should happen, when, and whether it is done."
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


ALLOWED_RECURRENCE = {"none", "daily", "weekly"}
ALLOWED_STATUS = {"active", "completed", "cancelled"}

# How many historical runs to keep per task. Old ones roll off so
# tasks.json stays small even after a year of daily firings.
RUN_HISTORY_CAP = 20


class TaskStore:
    """Tiny JSON-backed task database."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = root / "tasks.json"
        if not self.path.exists():
            self._write([])

    def all(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return []
        return data if isinstance(data, list) else []

    def create(
        self,
        title: str,
        description: str = "",
        due_at: str | None = None,
        recurrence: str = "none",
        notify: bool = False,
    ) -> dict[str, Any]:
        recurrence = recurrence or "none"
        if recurrence not in ALLOWED_RECURRENCE:
            raise ValueError(f"recurrence must be one of {sorted(ALLOWED_RECURRENCE)}")
        now = datetime.now().isoformat(timespec="seconds")
        tasks = self.all()
        task = {
            "id": self._unique_id(title, tasks),
            "title": title.strip(),
            "description": description.strip(),
            "status": "active",
            "due_at": self._normalize_datetime(due_at) if due_at else None,
            "recurrence": recurrence,
            "notify": bool(notify),
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
            "last_result": "",
            "last_runs": [],
        }
        tasks.append(task)
        self._write(tasks)
        return task

    def list(self, status: str = "active") -> list[dict[str, Any]]:
        if status != "all" and status not in ALLOWED_STATUS:
            raise ValueError("status must be active, completed, cancelled, or all")
        tasks = self.all()
        if status != "all":
            tasks = [t for t in tasks if t.get("status") == status]
        return sorted(tasks, key=lambda t: t.get("due_at") or "9999")

    def due(self, now: datetime | None = None) -> list[dict[str, Any]]:
        now = now or datetime.now()
        due_tasks: list[dict[str, Any]] = []
        for task in self.list("active"):
            due_at = task.get("due_at")
            if not due_at:
                continue
            target = datetime.fromisoformat(due_at)
            if target <= now:
                due_tasks.append(task)
        return due_tasks

    def next_due_seconds(self, now: datetime | None = None) -> float | None:
        now = now or datetime.now()
        seconds: list[float] = []
        for task in self.list("active"):
            due_at = task.get("due_at")
            if not due_at:
                continue
            target = datetime.fromisoformat(due_at)
            delta = (target - now).total_seconds()
            if delta > 0:
                seconds.append(delta)
        return min(seconds) if seconds else None

    def complete(self, task_id: str, result: str = "", duration_s: float | None = None) -> dict[str, Any]:
        tasks = self.all()
        task = self._find(tasks, task_id)
        now = datetime.now()
        task["last_result"] = result.strip()
        task["updated_at"] = now.isoformat(timespec="seconds")
        task["completed_at"] = now.isoformat(timespec="seconds")
        self._append_run(task, now, "success", result, duration_s)

        recurrence = task.get("recurrence", "none")
        if recurrence in {"daily", "weekly"}:
            task["due_at"] = self._advance_due(task.get("due_at"), recurrence, now)
            task["status"] = "active"
        else:
            task["status"] = "completed"
        self._write(tasks)
        return task

    def record_failure(self, task_id: str, error: str, duration_s: float | None = None) -> dict[str, Any]:
        """Log a failed run without changing status or advancing recurrence.

        Use this when a due task throws or the agent couldn't deliver.
        Status stays active (heartbeat will retry on next tick) but the
        failure shows up in last_runs so reliability is auditable.
        """
        tasks = self.all()
        task = self._find(tasks, task_id)
        now = datetime.now()
        task["updated_at"] = now.isoformat(timespec="seconds")
        task["last_result"] = error.strip()
        self._append_run(task, now, "failure", error, duration_s)
        self._write(tasks)
        return task

    def update(
        self,
        task_id: str,
        title: str | None = None,
        description: str | None = None,
        due_at: str | None = None,
        recurrence: str | None = None,
        notify: bool | None = None,
    ) -> dict[str, Any]:
        """Edit task metadata. None means "don't change this field"."""
        tasks = self.all()
        task = self._find(tasks, task_id)
        if title is not None:
            task["title"] = title.strip()
        if description is not None:
            task["description"] = description.strip()
        if due_at is not None:
            task["due_at"] = self._normalize_datetime(due_at)
        if recurrence is not None:
            if recurrence not in ALLOWED_RECURRENCE:
                raise ValueError(f"recurrence must be one of {sorted(ALLOWED_RECURRENCE)}")
            task["recurrence"] = recurrence
        if notify is not None:
            task["notify"] = bool(notify)
        task["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self._write(tasks)
        return task

    def delete(self, task_id: str) -> None:
        tasks = self.all()
        remaining = [t for t in tasks if t.get("id") != task_id]
        if len(remaining) == len(tasks):
            raise KeyError(f"task '{task_id}' not found")
        self._write(remaining)

    def run_now(self, task_id: str) -> dict[str, Any]:
        """Set due_at to now so heartbeat fires on its next tick."""
        tasks = self.all()
        task = self._find(tasks, task_id)
        task["due_at"] = datetime.now().isoformat(timespec="seconds")
        task["status"] = "active"
        task["updated_at"] = task["due_at"]
        self._write(tasks)
        return task

    def cancel(self, task_id: str, reason: str = "") -> dict[str, Any]:
        tasks = self.all()
        task = self._find(tasks, task_id)
        now = datetime.now().isoformat(timespec="seconds")
        task["status"] = "cancelled"
        task["updated_at"] = now
        task["last_result"] = reason.strip()
        self._write(tasks)
        return task

    @staticmethod
    def _append_run(
        task: dict[str, Any],
        ts: datetime,
        status: str,
        result: str,
        duration_s: float | None,
    ) -> None:
        run = {
            "ts": ts.isoformat(timespec="seconds"),
            "status": status,
            "result": (result or "").strip()[:500],
        }
        if duration_s is not None:
            run["duration_s"] = round(duration_s, 2)
        runs = task.setdefault("last_runs", [])
        runs.append(run)
        if len(runs) > RUN_HISTORY_CAP:
            del runs[: len(runs) - RUN_HISTORY_CAP]

    def schedule(
        self,
        task_id: str,
        due_at: str,
        recurrence: str | None = None,
    ) -> dict[str, Any]:
        tasks = self.all()
        task = self._find(tasks, task_id)
        if recurrence is not None:
            if recurrence not in ALLOWED_RECURRENCE:
                raise ValueError(f"recurrence must be one of {sorted(ALLOWED_RECURRENCE)}")
            task["recurrence"] = recurrence
        task["due_at"] = self._normalize_datetime(due_at)
        task["status"] = "active"
        task["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self._write(tasks)
        return task

    def _write(self, tasks: list[dict[str, Any]]) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(tasks, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def _find(self, tasks: list[dict[str, Any]], task_id: str) -> dict[str, Any]:
        for task in tasks:
            if task.get("id") == task_id:
                return task
        raise KeyError(f"task '{task_id}' not found")

    @staticmethod
    def _normalize_datetime(value: str | None) -> str | None:
        if value is None:
            return None
        target = datetime.fromisoformat(value)
        if target.tzinfo is not None:
            target = target.astimezone().replace(tzinfo=None)
        return target.isoformat(timespec="seconds")

    @staticmethod
    def _advance_due(due_at: str | None, recurrence: str, now: datetime) -> str:
        base = datetime.fromisoformat(due_at) if due_at else now
        step = timedelta(days=1 if recurrence == "daily" else 7)
        while base <= now:
            base += step
        return base.isoformat(timespec="seconds")

    @staticmethod
    def _unique_id(title: str, tasks: list[dict[str, Any]]) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40] or "task"
        existing = {t.get("id") for t in tasks}
        candidate = slug
        i = 2
        while candidate in existing:
            candidate = f"{slug}-{i}"
            i += 1
        return candidate
