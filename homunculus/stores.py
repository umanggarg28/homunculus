"""Small single-purpose stores extracted from Memory (Bundle 2 #2).

Each class owns one file under the agent's memory dir. Keeping them
in their own module makes the seams explicit and lets tests target a
single store without booting the rest of Memory.

  WorldStateStore  → _world_state.json  (typed JSON object, agent
                                          maintains during multi-step
                                          tasks; survives restart)
  NextTickStore    → _next_tick.txt     (agent-self-scheduled heartbeat
                                          wake; pop-on-read so stale
                                          values can't persist)
  ReflectionStore  → _last_reflection.txt  (YYYY-MM-DD of last
                                          daily reflection — gates
                                          the once-per-day reflect tick)
  SkillStatsStore  → _skill_stats.json  (per-skill execution telemetry —
                                          use counts, failure streaks, a
                                          capped run log; kept OUT of the
                                          authored skill_*.md so the file
                                          stays clean content the agent
                                          edits via the proposal flow)
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from homunculus.locking import file_lock as _flock


# ---- world state -------------------------------------------------------

class WorldStateStore:
    """Typed JSON object tracking what the agent is doing right now.

    Conventional keys (all free-form, agent-defined):
      focus         — short description of current goal
      active_task   — task_id currently being executed
      step          — step number in a multi-step flow
      last_action   — last tool called
      last_ok       — bool, did the last action succeed
      notes         — scratch pad for mid-task context
      updated_at    — ISO timestamp (auto-set by update())

    Persisted as a single JSON object via atomic rename so the heartbeat
    and web UI can read consistent snapshots without coordination.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    @property
    def path(self) -> Path:
        return self.root / "_world_state.json"

    def read(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def update(self, updates: dict) -> dict:
        """Merge `updates` into the world state and persist atomically.
        Returns the resulting full state."""
        with _flock(self.path.with_suffix(".json.lock")):
            state = self.read()
            state.update(updates)
            state["updated_at"] = datetime.now().isoformat(timespec="seconds")
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        return state

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()


# ---- next tick (self-scheduled heartbeat wake) ------------------------

class NextTickStore:
    """Single-line ISO datetime for the agent's next self-scheduled wake.

    pop() (not peek()) is the consume path: each tick starts fresh so a
    stale schedule can't pin the heartbeat to a long-past time after
    the daemon restarts. peek() is for UI display only.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    @property
    def path(self) -> Path:
        return self.root / "_next_tick.txt"

    def set(self, iso_datetime: str) -> None:
        self.path.write_text(iso_datetime.strip(), encoding="utf-8")

    def peek(self) -> str | None:
        if not self.path.exists():
            return None
        try:
            return self.path.read_text(encoding="utf-8").strip() or None
        except Exception:
            return None

    def pop(self) -> str | None:
        if not self.path.exists():
            return None
        try:
            value = self.path.read_text(encoding="utf-8").strip()
        finally:
            self.path.unlink()
        return value or None


# ---- daily reflection date --------------------------------------------

class ReflectionStore:
    """YYYY-MM-DD of the last day a reflection tick ran.

    The heartbeat consults this to decide whether the next tick should
    review yesterday's logs and save feedback memories, or run a normal
    proactive tick. One reflection per calendar day.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    @property
    def path(self) -> Path:
        return self.root / "_last_reflection.txt"

    def last_date(self) -> str | None:
        if not self.path.exists():
            return None
        value = self.path.read_text(encoding="utf-8").strip()
        return value or None

    def mark(self, date_str: str) -> None:
        """Mark a YYYY-MM-DD as having had its reflection done."""
        self.path.write_text(date_str.strip(), encoding="utf-8")


# ---- skill execution telemetry ----------------------------------------

# Per skill, keep at most this many run-log entries. A skill that has run
# hundreds of times doesn't need its full history here — the recent tail
# is enough to spot a failure streak; older detail lives in the trace logs.
_MAX_RUN_LOG = 20


class SkillStatsStore:
    """Execution telemetry for skills, keyed by skill name (``skill_<slug>``).

    This is the home for the *runtime* facts about a skill — how many
    times it has run, its current consecutive-failure streak, and a short
    run log — none of which belong in the authored ``skill_*.md``. Keeping
    them here means a skill file is pure content the agent authors and
    refines through the proposal flow, never machine-mutated in place.

    One JSON object, ``{skill_name: {uses, consecutive_failures,
    last_outcome, last_rated_at, run_log: [...]}}``, written atomically so
    the heartbeat and the web UI read consistent snapshots.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    @property
    def path(self) -> Path:
        return self.root / "_skill_stats.json"

    def _read_all(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def get(self, name: str) -> dict:
        """Stats for one skill, or an empty dict if it has never been rated."""
        return self._read_all().get(name, {})

    def all(self) -> dict:
        """Full ``{skill_name: stats}`` snapshot (read-only copy)."""
        return self._read_all()

    def seed_if_absent(self, name: str, *, uses: int, consecutive_failures: int) -> None:
        """Seed a skill's stats from legacy in-file counters — but only if the
        store has no entry yet, so a live sidecar value is never overwritten.
        Used by the one-time skill-file migration."""
        with _flock(self.path.with_suffix(".json.lock")):
            data = self._read_all()
            if name in data:
                return
            data[name] = {
                "uses": int(uses),
                "consecutive_failures": int(consecutive_failures),
                "last_outcome": None,
                "last_rated_at": None,
                "run_log": [],
            }
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(self.path)

    def record(self, name: str, outcome: str, notes: str = "") -> dict:
        """Record a ``success`` or ``failure`` against ``name``.

        Increments ``uses``; resets ``consecutive_failures`` to 0 on
        success or increments it on failure; appends a capped run-log
        entry. Returns the skill's resulting stats. Unknown outcomes are
        ignored (return current stats unchanged).
        """
        if outcome not in ("success", "failure"):
            return self.get(name)
        with _flock(self.path.with_suffix(".json.lock")):
            data = self._read_all()
            entry = data.get(name, {})
            uses = int(entry.get("uses", 0)) + 1
            consec = 0 if outcome == "success" else int(entry.get("consecutive_failures", 0)) + 1
            now = datetime.now()
            run_entry = {"at": now.strftime("%Y-%m-%d %H:%M"), "outcome": outcome}
            if notes:
                run_entry["notes"] = notes
            run_log = list(entry.get("run_log", []))
            run_log.append(run_entry)
            entry = {
                "uses": uses,
                "consecutive_failures": consec,
                "last_outcome": outcome,
                "last_rated_at": now.isoformat(timespec="seconds"),
                "run_log": run_log[-_MAX_RUN_LOG:],
            }
            data[name] = entry
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(self.path)
            return entry
