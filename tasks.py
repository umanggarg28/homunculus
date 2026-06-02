"""Structured task state for Homunculus.

Tasks are operational state, not semantic memory. Memory says "what is true";
tasks say "what should happen, when, and whether it is done."

Concurrency: the JSON file is read-modify-write, which races between
heartbeat (firing tasks) and the web UI / Telegram (creating / editing
them). All mutators go through `_with_lock()` which holds an exclusive
fcntl flock on a sidecar lock file for the duration of the RMW.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator


ALLOWED_RECURRENCE = {"none", "daily", "weekly"}
ALLOWED_STATUS = {"active", "completed", "cancelled"}

# How many historical runs to keep per task. Old ones roll off so
# tasks.json stays small even after a year of daily firings.
RUN_HISTORY_CAP = 20

# After N consecutive failures the task auto-cancels. Without a circuit
# breaker, a broken task fires every heartbeat tick forever and spams
# the logs / wastes LLM quota.
CONSECUTIVE_FAILURE_LIMIT = 3

# A task that already fired within this window is suppressed from a
# subsequent `due()` call. Prevents heartbeat-restart double-fires:
# heartbeat crashes mid-tick → next tick sees the same overdue task →
# fires it again without this check.
RE_FIRE_SUPPRESSION_SECONDS = 30 * 60  # 30 minutes — covers provider outages without duplicate notifications

# `_advance_due()` can in theory loop forever if `recurrence_step` is
# 0 or if `now` keeps advancing during a slow operation. Cap iterations.
ADVANCE_DUE_MAX_ITERS = 366 * 2  # ~2 years of daily steps


class TaskStore:
    """Tiny JSON-backed task database."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = root / "tasks.json"
        self.lock_path = root / "tasks.json.lock"
        if not self.path.exists():
            self._write([])

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """Hold an exclusive flock on the sidecar lock file.

        Used around every read-modify-write so that concurrent updates
        (heartbeat completing a task while the user edits its due date
        from the web UI) don't clobber each other.
        """
        # Open in append-mode so we don't truncate the file. Closing
        # releases the lock.
        with self.lock_path.open("a") as f:
            for attempt in range(50):  # ~5s total at 100ms sleep
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as e:
                    if e.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
                        raise
                    time.sleep(0.1)
            else:
                raise RuntimeError(
                    "Could not acquire tasks.json lock after 5s — "
                    "another process is holding it too long."
                )
            try:
                yield
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

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
        success_criteria: list | None = None,
    ) -> dict[str, Any]:
        recurrence = recurrence or "none"
        if recurrence not in ALLOWED_RECURRENCE:
            raise ValueError(f"recurrence must be one of {sorted(ALLOWED_RECURRENCE)}")
        with self._locked():
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
                # Dedupe + circuit breaker fields.
                "last_fired_at": None,
                "consecutive_failures": 0,
                # Machine-checked before complete_task is accepted.
                "success_criteria": success_criteria or [],
            }
            tasks.append(task)
            self._write(tasks)
            return task

    def get(self, task_id: str) -> dict[str, Any] | None:
        """Return the task dict for task_id, or None if not found."""
        for t in self.all():
            if t.get("id") == task_id:
                return t
        return None

    def list(self, status: str = "active") -> list[dict[str, Any]]:
        if status != "all" and status not in ALLOWED_STATUS:
            raise ValueError("status must be active, completed, cancelled, or all")
        tasks = self.all()
        if status != "all":
            tasks = [t for t in tasks if t.get("status") == status]
        return sorted(tasks, key=lambda t: t.get("due_at") or "9999")

    def due(self, now: datetime | None = None) -> list[dict[str, Any]]:
        """Return active tasks whose due_at is <= now AND haven't fired
        recently (within RE_FIRE_SUPPRESSION_SECONDS). The suppression
        check prevents heartbeat-restart double-fires.
        """
        now = now or datetime.now()
        due_tasks: list[dict[str, Any]] = []
        for task in self.list("active"):
            due_at = task.get("due_at")
            if not due_at:
                continue
            try:
                target = datetime.fromisoformat(due_at)
            except ValueError:
                continue  # malformed due_at — skip rather than crash
            if target > now:
                continue
            # Skip tasks that are currently executing (heartbeat restart mid-tick).
            if task.get("executing"):
                continue
            last_fired = task.get("last_fired_at")
            if last_fired:
                try:
                    last_fired_dt = datetime.fromisoformat(last_fired)
                except ValueError:
                    last_fired_dt = None
                if last_fired_dt is not None:
                    age = (now - last_fired_dt).total_seconds()
                    if age < RE_FIRE_SUPPRESSION_SECONDS:
                        continue
            due_tasks.append(task)
        return due_tasks

    def mark_fired(self, task_id: str, now: datetime | None = None) -> dict[str, Any]:
        """Stamp `last_fired_at` and set `executing=True` before the agent
        runs the task. Prevents double-fire if heartbeat restarts mid-tick.
        `executing` is cleared by record_success/record_failure/complete.
        """
        now = now or datetime.now()
        with self._locked():
            tasks = self.all()
            task = self._find(tasks, task_id)
            task["last_fired_at"] = now.isoformat(timespec="seconds")
            task["executing"] = True
            self._write(tasks)
            return task

    def next_due_seconds(self, now: datetime | None = None) -> float | None:
        now = now or datetime.now()
        seconds: list[float] = []
        for task in self.list("active"):
            due_at = task.get("due_at")
            if not due_at:
                continue
            try:
                target = datetime.fromisoformat(due_at)
            except ValueError:
                continue
            delta = (target - now).total_seconds()
            if delta > 0:
                seconds.append(delta)
        return min(seconds) if seconds else None

    def complete(self, task_id: str, result: str = "", duration_s: float | None = None) -> dict[str, Any]:
        with self._locked():
            tasks = self.all()
            task = self._find(tasks, task_id)
            now = datetime.now()
            task["last_result"] = result.strip()
            task["updated_at"] = now.isoformat(timespec="seconds")
            task["completed_at"] = now.isoformat(timespec="seconds")
            task["consecutive_failures"] = 0  # successful run resets counter
            task["executing"] = False
            self._append_run(task, now, "success", result, duration_s)

            recurrence = task.get("recurrence", "none")
            if recurrence in {"daily", "weekly"}:
                task["due_at"] = self._advance_due(task.get("due_at"), recurrence, now)
                task["status"] = "active"
            else:
                task["status"] = "completed"
            self._write(tasks)
            return task

    def record_failure(
        self,
        task_id: str,
        error: str,
        duration_s: float | None = None,
        increment_failures: bool = True,
    ) -> dict[str, Any]:
        """Log a failed run.

        When increment_failures=True (default), increments consecutive_failures
        and auto-cancels at CONSECUTIVE_FAILURE_LIMIT. Pass False for transient
        infrastructure failures (provider exhaustion) that don't indicate a
        broken task — the run is still logged but the auto-cancel counter is
        left unchanged.
        """
        with self._locked():
            tasks = self.all()
            task = self._find(tasks, task_id)
            now = datetime.now()
            task["updated_at"] = now.isoformat(timespec="seconds")
            task["last_result"] = error.strip()
            task["executing"] = False
            self._append_run(task, now, "failure", error, duration_s)
            if increment_failures:
                failures = int(task.get("consecutive_failures", 0)) + 1
                task["consecutive_failures"] = failures
                if failures >= CONSECUTIVE_FAILURE_LIMIT:
                    task["status"] = "cancelled"
                    task["last_result"] = (
                        f"auto-cancelled after {failures} consecutive failures · "
                        f"last error: {error.strip()[:200]}"
                    )
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
        success_criteria: list | None = None,
    ) -> dict[str, Any]:
        """Edit task metadata. None means "don't change this field"."""
        with self._locked():
            tasks = self.all()
            task = self._find(tasks, task_id)
            if title is not None:
                task["title"] = title.strip()
            if description is not None:
                task["description"] = description.strip()
            if due_at is not None:
                task["due_at"] = self._normalize_datetime(due_at)
                # User-edited due_at clears the fire stamp so the new
                # schedule isn't suppressed by an old fire.
                task["last_fired_at"] = None
            if recurrence is not None:
                if recurrence not in ALLOWED_RECURRENCE:
                    raise ValueError(f"recurrence must be one of {sorted(ALLOWED_RECURRENCE)}")
                task["recurrence"] = recurrence
            if notify is not None:
                task["notify"] = bool(notify)
            if success_criteria is not None:
                task["success_criteria"] = success_criteria
            task["updated_at"] = datetime.now().isoformat(timespec="seconds")
            self._write(tasks)
            return task

    def delete(self, task_id: str) -> None:
        with self._locked():
            tasks = self.all()
            remaining = [t for t in tasks if t.get("id") != task_id]
            if len(remaining) == len(tasks):
                raise KeyError(f"task '{task_id}' not found")
            self._write(remaining)

    def run_now(self, task_id: str) -> dict[str, Any]:
        """Set due_at to now so heartbeat fires on its next tick."""
        with self._locked():
            tasks = self.all()
            task = self._find(tasks, task_id)
            now_iso = datetime.now().isoformat(timespec="seconds")
            task["due_at"] = now_iso
            task["status"] = "active"
            task["updated_at"] = now_iso
            task["last_fired_at"] = None  # clear so it fires immediately
            self._write(tasks)
            return task

    def cancel(self, task_id: str, reason: str = "") -> dict[str, Any]:
        with self._locked():
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
        with self._locked():
            tasks = self.all()
            task = self._find(tasks, task_id)
            if recurrence is not None:
                if recurrence not in ALLOWED_RECURRENCE:
                    raise ValueError(f"recurrence must be one of {sorted(ALLOWED_RECURRENCE)}")
                task["recurrence"] = recurrence
            task["due_at"] = self._normalize_datetime(due_at)
            task["status"] = "active"
            task["updated_at"] = datetime.now().isoformat(timespec="seconds")
            task["last_fired_at"] = None  # rescheduled tasks fire fresh
            self._write(tasks)
            return task

    def _write(self, tasks: list[dict[str, Any]]) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(tasks, indent=2), encoding="utf-8")
        # Atomic rename + fsync the parent dir so the new metadata is
        # durable. Without dirfsync, a crash between rename and flush
        # can leave the directory entry pointing to the old inode.
        tmp.replace(self.path)
        try:
            dir_fd = os.open(str(self.root), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass  # best-effort; tmpfs / non-POSIX may not support this

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
        """Advance the next due timestamp until it lands past `now`.

        Capped at ADVANCE_DUE_MAX_ITERS to guard against pathological
        inputs (zero step, broken clock skew, etc.) so we don't loop
        forever inside a `complete()` call holding the file lock.
        """
        base = datetime.fromisoformat(due_at) if due_at else now
        step = timedelta(days=1 if recurrence == "daily" else 7)
        for _ in range(ADVANCE_DUE_MAX_ITERS):
            if base > now:
                return base.isoformat(timespec="seconds")
            base += step
        # If we somehow couldn't advance past `now`, fall back to a step
        # ahead of now so the next fire is in the future.
        return (now + step).isoformat(timespec="seconds")

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
