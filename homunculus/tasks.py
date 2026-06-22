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

from homunculus.config import get_config
from homunculus.user_tz import now_user_naive
from typing import Any
from collections.abc import Iterator


ALLOWED_RECURRENCE = {"none", "daily", "weekly"}
ALLOWED_STATUS = {"active", "completed", "cancelled"}

# All numeric tunables for task lifecycle live in
# HomunculusConfig.task (see config.py). Each method below reads them
# via `get_config().task.*` so tests can override via set_config(...)
# without monkey-patching this module.
#
# Knobs and what they do:
#   run_history_cap                — how many runs we keep per task
#   consecutive_failure_limit      — auto-cancel after N failures
#   partial_retry_minutes          — partials retry this many min later
#   max_consecutive_partials       — escalate after N partials in a row
#   re_fire_suppression_seconds    — dedup window for due() (restart-safe)
#   advance_due_max_iters          — defence against pathological inputs


def scratchpad_dir(tasks_root: Path) -> Path:
    """Where per-task scratchpads live. Lazily created on first write."""
    return tasks_root / "scratchpads"


def scratchpad_path(tasks_root: Path, task_id: str) -> Path:
    """Filesystem path for a task's scratchpad. Filename mirrors task_id
    to avoid collisions; .md extension keeps the file legible to the
    user via the dashboard's file browser."""
    safe = re.sub(r"[^a-zA-Z0-9._-]", "_", task_id)
    return scratchpad_dir(tasks_root) / f"{safe}.md"


def read_scratchpad(tasks_root: Path, task_id: str) -> str:
    """Return the scratchpad content for a task, or empty string if none.
    Never raises — a missing scratchpad is just an empty result."""
    p = scratchpad_path(tasks_root, task_id)
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


def write_scratchpad(tasks_root: Path, task_id: str, content: str) -> Path:
    """Overwrite a task's scratchpad with `content`. Returns the path
    written. Creates the parent directory on first write."""
    p = scratchpad_path(tasks_root, task_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def append_scratchpad(tasks_root: Path, task_id: str, content: str) -> Path:
    """Append `content` to a task's scratchpad (creating it if needed).
    Each append is separated by a newline so successive notes don't
    smash together."""
    p = scratchpad_path(tasks_root, task_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    prefix = "" if not p.exists() or p.read_text(encoding="utf-8").endswith("\n") else "\n"
    with p.open("a", encoding="utf-8") as f:
        f.write(prefix + content + ("\n" if not content.endswith("\n") else ""))
    return p


def clear_scratchpad(tasks_root: Path, task_id: str) -> None:
    """Remove a task's scratchpad. Called when complete_task succeeds —
    the work is delivered, the scratchpad would only confuse the next
    recurring run. No-op if the file doesn't exist."""
    p = scratchpad_path(tasks_root, task_id)
    try:
        p.unlink()
    except FileNotFoundError:
        pass


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
            for _attempt in range(50):  # ~5s total at 100ms sleep
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
        skill: str | None = None,
    ) -> dict[str, Any]:
        recurrence = recurrence or "none"
        if recurrence not in ALLOWED_RECURRENCE:
            raise ValueError(f"recurrence must be one of {sorted(ALLOWED_RECURRENCE)}")
        with self._locked():
            now = now_user_naive().isoformat(timespec="seconds")
            tasks = self.all()
            normalized_due = self._normalize_datetime(due_at) if due_at else None
            # Anchor the recurring time-of-day at creation. _advance_due
            # snaps each next-cycle due_at to this wall-clock time so
            # partial-recovery shifts (mark_partial → due_at advances by
            # +10min) don't permanently drift a "daily at 7 AM" task to
            # firing at 1:40 AM after a few weeks of retries.
            recur_anchor = self._compute_recur_anchor(normalized_due, recurrence)
            task = {
                "id": self._unique_id(title, tasks),
                "title": title.strip(),
                "description": description.strip(),
                "status": "active",
                "due_at": normalized_due,
                "recurrence": recurrence,
                "recur_anchor": recur_anchor,
                "notify": bool(notify),
                "created_at": now,
                "updated_at": now,
                "completed_at": None,
                "last_result": "",
                "last_runs": [],
                # Dedupe + circuit breaker fields.
                "last_fired_at": None,
                "consecutive_failures": 0,
                # Resumable-task state — tracks runs that made partial
                # progress but didn't complete. Reset by complete().
                "consecutive_partials": 0,
                # Machine-checked before complete_task is accepted.
                "success_criteria": success_criteria or [],
                # Optional skill_<name> playbook this task should run
                # under. When set + the skill has a `states:` frontmatter
                # block, heartbeat drives the agent through the state
                # machine instead of the free-form loop.
                "skill": skill,
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
        recently (within config.task.re_fire_suppression_seconds). The suppression
        check prevents heartbeat-restart double-fires.
        """
        now = now or now_user_naive()
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
                    if age < get_config().task.re_fire_suppression_seconds:
                        continue
            due_tasks.append(task)
        return due_tasks

    def mark_fired(self, task_id: str, now: datetime | None = None) -> dict[str, Any]:
        """Stamp `last_fired_at` and set `executing=True` before the agent
        runs the task. Prevents double-fire if heartbeat restarts mid-tick.
        `executing` is cleared by record_success/record_failure/complete.
        """
        now = now or now_user_naive()
        with self._locked():
            tasks = self.all()
            task = self._find(tasks, task_id)
            task["last_fired_at"] = now.isoformat(timespec="seconds")
            task["executing"] = True
            self._write(tasks)
            return task

    def next_due_seconds(self, now: datetime | None = None) -> float | None:
        now = now or now_user_naive()
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

    def complete(
        self,
        task_id: str,
        result: str = "",
        duration_s: float | None = None,
        usage: dict | None = None,
    ) -> dict[str, Any]:
        with self._locked():
            tasks = self.all()
            task = self._find(tasks, task_id)
            now = now_user_naive()
            task["last_result"] = result.strip()
            task["updated_at"] = now.isoformat(timespec="seconds")
            task["completed_at"] = now.isoformat(timespec="seconds")
            task["consecutive_failures"] = 0  # successful run resets counter
            task["consecutive_partials"] = 0  # successful run also resets partials
            task["executing"] = False
            self._append_run(task, now, "success", result, duration_s, usage)

            recurrence = task.get("recurrence", "none")
            if recurrence in {"daily", "weekly"}:
                task["due_at"] = self._advance_due(
                    task.get("due_at"), recurrence, now, task.get("recur_anchor"),
                )
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
        usage: dict | None = None,
    ) -> dict[str, Any]:
        """Log a failed run.

        When increment_failures=True (default), increments consecutive_failures
        and auto-cancels at config.task.consecutive_failure_limit. Pass False for transient
        infrastructure failures (provider exhaustion) that don't indicate a
        broken task — the run is still logged but the auto-cancel counter is
        left unchanged.
        """
        with self._locked():
            tasks = self.all()
            task = self._find(tasks, task_id)
            now = now_user_naive()
            task["updated_at"] = now.isoformat(timespec="seconds")
            task["last_result"] = error.strip()
            task["executing"] = False
            self._append_run(task, now, "failure", error, duration_s, usage)
            if increment_failures:
                failures = int(task.get("consecutive_failures", 0)) + 1
                task["consecutive_failures"] = failures
                if failures >= get_config().task.consecutive_failure_limit:
                    task["status"] = "cancelled"
                    task["last_result"] = (
                        f"auto-cancelled after {failures} consecutive failures · "
                        f"last error: {error.strip()[:200]}"
                    )
                else:
                    # Advance due_at on real (non-transient) failures of
                    # recurring tasks. Otherwise due_at stays in the past
                    # and the task re-fires on every heartbeat tick →
                    # failure-notify spam until consecutive_failures hits
                    # the auto-cancel limit. Transient failures (provider
                    # exhaustion etc.) come in with increment_failures=
                    # False and DO retry on the next tick, which is what
                    # the user actually wants for those.
                    recurrence = task.get("recurrence", "none")
                    if recurrence in {"daily", "weekly"}:
                        task["due_at"] = self._advance_due(
                            task.get("due_at"), recurrence, now,
                            task.get("recur_anchor"),
                        )
            self._write(tasks)
            return task

    def mark_partial(
        self,
        task_id: str,
        reason: str,
        duration_s: float | None = None,
        advance_minutes: int | None = None,
        usage: dict | None = None,
    ) -> dict[str, Any]:
        """Log a partial run — work in progress, not yet complete.

        Used when a task ran out of iterations / hit provider exhaustion /
        explicitly called continue_task. The task's scratchpad survives
        so the next attempt can resume, and due_at advances by a SHORT
        interval (default 10 min) instead of the full recurrence step.

        After config.task.max_consecutive_partials in a row without an intervening
        completion, escalates to a real failure so the user finds out.

        Returns the updated task. The caller can inspect `status` —
        "active" means it's been rescheduled, "active" with
        consecutive_failures > 0 means it escalated.
        """
        cfg = get_config().task
        if advance_minutes is None:
            advance_minutes = cfg.partial_retry_minutes
        with self._locked():
            tasks = self.all()
            task = self._find(tasks, task_id)
            now = now_user_naive()
            task["updated_at"] = now.isoformat(timespec="seconds")
            task["last_result"] = f"partial: {reason.strip()}"
            task["executing"] = False
            self._append_run(task, now, "partial", reason, duration_s, usage)

            partials = int(task.get("consecutive_partials", 0)) + 1
            task["consecutive_partials"] = partials

            if partials >= cfg.max_consecutive_partials:
                # Escalate. Use record_failure inline so consecutive_
                # failures advances and consecutive_failure_limit logic
                # still applies. We reset partials so the next escalation
                # is also a fresh count, not a tail effect.
                task["consecutive_partials"] = 0
                failures = int(task.get("consecutive_failures", 0)) + 1
                task["consecutive_failures"] = failures
                task["last_result"] = (
                    f"escalated: {partials} consecutive partials. "
                    f"Last reason: {reason.strip()[:200]}"
                )
                if failures >= cfg.consecutive_failure_limit:
                    task["status"] = "cancelled"
                else:
                    recurrence = task.get("recurrence", "none")
                    if recurrence in {"daily", "weekly"}:
                        task["due_at"] = self._advance_due(
                            task.get("due_at"), recurrence, now,
                            task.get("recur_anchor"),
                        )
            else:
                # Reschedule for the near future so the next tick can
                # resume from the scratchpad. We bypass _advance_due
                # because we want a short delta, not a recurrence step.
                task["due_at"] = (
                    now + timedelta(minutes=advance_minutes)
                ).isoformat(timespec="seconds")
                # Clear the dedupe stamp so the new due_at fires on the
                # very next tick — otherwise config.task.re_fire_suppression_seconds
                # would block it for 30 min even though we want it
                # back in ~10 min.
                task["last_fired_at"] = None

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
        skill: str | None = None,
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
            if skill is not None:
                # Allow empty string to clear the skill linkage.
                task["skill"] = skill or None
            task["updated_at"] = now_user_naive().isoformat(timespec="seconds")
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
        """Set due_at to now so heartbeat fires on its next tick.

        Reactivating a cancelled task starts a fresh failure budget: a task
        auto-cancelled after consecutive failures would otherwise re-cancel on
        its very next failure, since the counter survives the cancel. Clearing
        it on reactivation makes "run now" a genuine reset, not a one-shot.
        """
        with self._locked():
            tasks = self.all()
            task = self._find(tasks, task_id)
            now_iso = now_user_naive().isoformat(timespec="seconds")
            if task.get("status") == "cancelled":
                task["consecutive_failures"] = 0
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
            now = now_user_naive().isoformat(timespec="seconds")
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
        usage: dict | None = None,
    ) -> None:
        """Append a run record; `usage` carries token + cost metrics.

        `usage` shape (all fields optional):
            input_tokens, output_tokens, cached_tokens, cost_cents,
            calls (number of LLM round-trips during this run)
        """
        run = {
            "ts": ts.isoformat(timespec="seconds"),
            "status": status,
            "result": (result or "").strip()[:500],
        }
        if duration_s is not None:
            run["duration_s"] = round(duration_s, 2)
        if usage:
            # Round cost to 4 decimals so a daily-brief that costs
            # 0.0023¢ doesn't render as 0.
            for key in ("input_tokens", "output_tokens", "cached_tokens", "calls"):
                if usage.get(key):
                    run[key] = int(usage[key])
            if usage.get("cost_cents"):
                run["cost_cents"] = round(float(usage["cost_cents"]), 4)
        runs = task.setdefault("last_runs", [])
        runs.append(run)
        cap = get_config().task.run_history_cap
        if len(runs) > cap:
            del runs[: len(runs) - cap]

    # How many delivery keys a task remembers. 200 daily items ≈ 6+
    # months of history — enough to cover any realistic content list
    # (Top Interview 150 fits with room to spare) without unbounded
    # growth of tasks.json.
    DELIVERED_KEYS_CAP = 200

    def record_delivery(self, task_id: str, key: str) -> dict[str, Any]:
        """Append a delivery key to the task's ledger.

        The ledger (`task["delivered"]`, a list of {key, ts}) is the
        harness-owned record of WHAT a recurring delivery task has
        already sent. It replaces the LLM-maintained tracker file,
        which degraded within weeks (format drift, missed updates →
        repeated deliveries). Keys are normalized to lowercase;
        duplicates are ignored so re-recording is idempotent.
        """
        key = (key or "").strip().lower()
        if not key:
            return self.get(task_id) or {}
        with self._locked():
            tasks = self.all()
            task = self._find(tasks, task_id)
            delivered = task.setdefault("delivered", [])
            if all(d.get("key") != key for d in delivered):
                delivered.append({
                    "key": key,
                    "ts": now_user_naive().isoformat(timespec="seconds"),
                })
                if len(delivered) > self.DELIVERED_KEYS_CAP:
                    del delivered[: len(delivered) - self.DELIVERED_KEYS_CAP]
                self._write(tasks)
            return task

    def attribute_usage_to_last_run(
        self,
        task_id: str,
        usage: dict,
    ) -> None:
        """Retrofit usage metrics onto the most recent run entry.

        Used by heartbeat after a successful complete_task call: the
        run was appended by the tool layer (which doesn't see usage),
        and we measure usage at the orchestration layer once the agent
        loop returns. Idempotent — re-attribution overwrites, doesn't
        accumulate. No-op if the task has no runs yet.
        """
        with self._locked():
            tasks = self.all()
            try:
                task = self._find(tasks, task_id)
            except KeyError:
                return
            runs = task.get("last_runs") or []
            if not runs:
                return
            run = runs[-1]
            for key in ("input_tokens", "output_tokens", "cached_tokens", "calls"):
                if usage.get(key):
                    run[key] = int(usage[key])
            if usage.get("cost_cents"):
                run["cost_cents"] = round(float(usage["cost_cents"]), 4)
            self._write(tasks)

    # How much of a delivery we keep for the reflection's quality
    # self-critique. Enough to judge structure (links, headers, per-item
    # shape) without bloating tasks.json.
    DELIVERED_TEXT_CAP = 1500

    def attribute_delivered_text_to_last_run(self, task_id: str, text: str) -> None:
        """Retrofit the actual notify() text the user received onto the most
        recent run. The daily reflection reads this to self-critique delivery
        QUALITY (fabricated links, thin content) — not just pass/fail, which
        the success_criteria already cover. Mirrors
        attribute_usage_to_last_run: idempotent, no-op if empty or no runs."""
        text = (text or "").strip()
        if not text:
            return
        with self._locked():
            tasks = self.all()
            try:
                task = self._find(tasks, task_id)
            except KeyError:
                return
            runs = task.get("last_runs") or []
            if not runs:
                return
            runs[-1]["delivered_text"] = text[: self.DELIVERED_TEXT_CAP]
            self._write(tasks)

    def attribute_tool_trace_to_last_run(self, task_id: str, trace: str) -> None:
        """Retrofit the run's tool-call trace (the sequence of tools the agent
        invoked) onto the most recent run. The daily reflection reads this to
        detect skill staleness that delivered_text/status can't show — e.g. a
        tool called repeatedly because the skill's handling of its result is
        out of date. Mirrors attribute_delivered_text_to_last_run: idempotent,
        no-op if empty or no runs."""
        trace = (trace or "").strip()
        if not trace:
            return
        with self._locked():
            tasks = self.all()
            try:
                task = self._find(tasks, task_id)
            except KeyError:
                return
            runs = task.get("last_runs") or []
            if not runs:
                return
            runs[-1]["tool_trace"] = trace[:1000]
            self._write(tasks)

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
            normalized_due = self._normalize_datetime(due_at)
            task["due_at"] = normalized_due
            # Recompute the recurrence anchor from the NEW due time.
            # Without this, rescheduling a recurring task to a new
            # time-of-day kept the stale anchor, and _advance_due snapped
            # every later cycle back to the old time (observed restoring
            # morning-brief to 10:00 — it would have reverted to 09:00).
            task["recur_anchor"] = self._compute_recur_anchor(normalized_due, task.get("recurrence", "none"))
            task["status"] = "active"
            task["updated_at"] = now_user_naive().isoformat(timespec="seconds")
            task["last_fired_at"] = None  # rescheduled tasks fire fresh
            self._write(tasks)
            return task

    @staticmethod
    def _compute_recur_anchor(normalized_due: str | None, recurrence: str) -> str | None:
        """The HH:MM:SS that _advance_due snaps each recurring cycle to,
        or None for non-recurring / undated tasks. Pinning the
        time-of-day at (re)schedule time keeps partial-recovery shifts
        from drifting a 'daily at 10 AM' task to odd hours."""
        if recurrence in ("daily", "weekly") and normalized_due:
            try:
                return datetime.fromisoformat(normalized_due).strftime("%H:%M:%S")
            except ValueError:
                pass
        return None

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
        """Normalize an ISO datetime to the store's canonical form:
        NAIVE wall-clock in the USER's timezone.

        tz-aware inputs convert to the user's zone before dropping
        tzinfo. The old code used astimezone() with no argument —
        CONTAINER-local, i.e. UTC in Docker — so an agent that
        correctly wrote "12:00+05:30" got stored as 06:30 and the
        reminder fired five and a half hours early (live 2026-06-11).
        """
        if value is None:
            return None
        target = datetime.fromisoformat(value)
        if target.tzinfo is not None:
            from homunculus.user_tz import get_user_tz
            tz = get_user_tz()
            target = (
                target.astimezone(tz) if tz else target.astimezone()
            ).replace(tzinfo=None)
        return target.isoformat(timespec="seconds")

    @staticmethod
    def _advance_due(
        due_at: str | None,
        recurrence: str,
        now: datetime,
        recur_anchor: str | None = None,
    ) -> str:
        """Advance the next due timestamp until it lands past `now`.

        Capped at config.task.advance_due_max_iters to guard against pathological
        inputs (zero step, broken clock skew, etc.) so we don't loop
        forever inside a `complete()` call holding the file lock.

        If `recur_anchor` (HH:MM:SS) is provided, the next due_at SNAPS
        to that wall-clock time on the next valid calendar day. Without
        anchor snapping, partial-recovery shifts (which advance due_at
        by +10 minutes) compound across cycles and silently drift a
        "daily at 7 AM" task into firing at 1:40 AM after a few weeks.
        With the anchor, the time-of-day is restored each cycle.
        """
        step = timedelta(days=1 if recurrence == "daily" else 7)

        if recur_anchor and recurrence in ("daily", "weekly"):
            # Anchor mode: next fire is the next anchor strictly after
            # `now`. Starts from today's anchor and only adds `step` if
            # today's anchor has already passed. The old implementation
            # unconditionally added one step, which made an early fire
            # (before today's anchor) silently skip today's slot —
            # e.g., a 2 AM manual test of a 9 AM daily task would
            # schedule the next run for TOMORROW 9 AM instead of TODAY
            # 9 AM, missing the user-facing delivery entirely.
            try:
                anchor_h, anchor_m, anchor_s = (int(x) for x in recur_anchor.split(":"))
                base = now.replace(
                    hour=anchor_h, minute=anchor_m, second=anchor_s, microsecond=0,
                )
                # Weekly must also land on the original due's WEEKDAY, not
                # merely the next anchor time-of-day. Without this, a
                # weekly task whose anchor time is later than `now`
                # reschedules to TODAY (≤24h out) instead of a week later
                # on the right day — observed live: a Saturday-night
                # weekly failing at 00:23 Sunday rescheduled to Sunday,
                # one day out, not the following Saturday.
                if recurrence == "weekly" and due_at:
                    try:
                        due_wd = datetime.fromisoformat(due_at).weekday()
                        base += timedelta(days=(due_wd - base.weekday()) % 7)
                    except ValueError:
                        pass
                while base <= now:
                    base += step
                return base.isoformat(timespec="seconds")
            except (ValueError, AttributeError):
                pass  # malformed anchor — fall through to legacy behaviour

        base = datetime.fromisoformat(due_at) if due_at else now
        for _ in range(get_config().task.advance_due_max_iters):
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


# --- Brief synthesis -----------------------------------------------------
#
# task_health_summary computes the deterministic morning-brief state.
# Lives here (not in tools/scheduling.py) so it has zero MCP / package
# coupling and can be unit-tested standalone. The MCP tool wrapper is a
# thin pass-through.

def task_health_summary(store: TaskStore) -> dict:
    """Return a snapshot for the morning brief.

    - today_commitments: active tasks whose due_at is within the next
      24 user-local hours.
    - alerts: active tasks whose MOST RECENT run is a failure within the
      last 36h. Tasks where the latest run is a success/partial are not
      alerts even if older runs failed.
    - recently_recovered: tasks with prior failures within the window
      that the latest run resolved (latest = success or partial).

    The LLM-driven brief used to read raw last_runs and judge for
    itself, which consistently misreported old failures as current. By
    centralizing the judgment here, the brief becomes deterministic.
    """
    now = now_user_naive()
    soon = now + timedelta(hours=24)
    failure_window = now - timedelta(hours=36)

    commitments: list[dict] = []
    alerts: list[dict] = []
    recovered: list[dict] = []

    for task in store.list("active"):
        title = task.get("title", task.get("id", "untitled"))

        due_at_raw = task.get("due_at")
        if due_at_raw:
            try:
                due_at = datetime.fromisoformat(due_at_raw)
                if now <= due_at <= soon:
                    commitments.append({
                        "id": task.get("id"),
                        "title": title,
                        "due_at": due_at_raw,
                    })
            except ValueError:
                pass

        runs = task.get("last_runs") or []
        if not runs:
            continue
        latest = runs[-1]
        try:
            latest_ts = datetime.fromisoformat(latest.get("ts", ""))
        except ValueError:
            continue

        latest_status = latest.get("status", "")
        if latest_status == "failure" and latest_ts >= failure_window:
            alerts.append({
                "id": task.get("id"),
                "title": title,
                "ts": latest.get("ts"),
                "result": (latest.get("result") or "")[:200],
            })
        elif latest_status in ("success", "partial"):
            prior_failures = [
                r for r in runs[:-1]
                if r.get("status") == "failure"
                and r.get("ts", "") >= failure_window.isoformat()
            ]
            if prior_failures:
                recovered.append({
                    "id": task.get("id"),
                    "title": title,
                    "latest_ts": latest.get("ts"),
                    "latest_status": latest_status,
                    "prior_failure_count": len(prior_failures),
                })

    return {
        "now": now.isoformat(timespec="seconds"),
        "today_commitments": commitments,
        "alerts": alerts,
        "recently_recovered": recovered,
    }
