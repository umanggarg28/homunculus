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
"""

from __future__ import annotations

import errno
import fcntl
import json
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from collections.abc import Iterator


# ---- shared lock helper -----------------------------------------------

@contextmanager
def _flock(lock_path: Path) -> Iterator[None]:
    """Exclusive fcntl flock on a sidecar lock file.

    Same shape as memory._file_lock — duplicated here so this module
    has no upward dependency on Memory.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a") as f:
        for _ in range(50):
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as e:
                if e.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
                    raise
                time.sleep(0.1)
        else:
            raise RuntimeError(f"could not acquire {lock_path.name} after 5s")
        try:
            yield
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


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
