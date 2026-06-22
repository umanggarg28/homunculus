"""NotificationQueue — heartbeat → user-visible chat bridge.

Background
----------
When `notify()` fires (typically from the heartbeat daemon), it sends a
Telegram message — but the in-memory Agent.history on the chat side
doesn't know about it. Without bridging, a follow-up like "explain it"
arrives with zero context and the agent confabulates an unrelated
answer.

We append every notification to this jsonl queue; the Telegram bot
(and the Web API stream) drain entries newer than
`_notifications_consumed_ts` into history before processing the next
user message.

Extracted from Memory as part of the god-class split (Bundle 2 #2,
Letta/Hermes pattern — single-purpose stores). Memory had grown to
1200+ LOC mixing markdown-memory CRUD, session pointers, world state,
notifications, archival sqlite, and reflection log. Each store has its
own file layout and lock semantics; keeping them in one class meant
every change had a wide blast radius. Splitting makes the seams
explicit and lets tests target a single store at a time.

File layout (all under `root/`):
  _notifications.jsonl           — append-only event log
  _notifications_consumed_ts.txt — drain pointer (unix-epoch float)
  _notifications.lock            — fcntl flock sidecar
"""

from __future__ import annotations

import errno
import fcntl
import json
import time
from contextlib import contextmanager
from pathlib import Path
from collections.abc import Iterator


class NotificationQueue:
    """Append-only notification queue with a consumption pointer.

    Single-writer-safe via O_APPEND for normal-sized messages (PIPE_BUF
    = 4096 bytes); the lock is only held around the drain pointer
    update so two drainers can't double-inject the same notifications.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    @property
    def log_path(self) -> Path:
        return self.root / "_notifications.jsonl"

    @property
    def pointer_path(self) -> Path:
        return self.root / "_notifications_consumed_ts.txt"

    @property
    def lock_path(self) -> Path:
        return self.root / "_notifications.lock"

    @contextmanager
    def _flock(self) -> Iterator[None]:
        """Exclusive fcntl flock on the lock sidecar.

        Same shape as memory._file_lock — duplicated here rather than
        imported so this module has no upward dependency on Memory.
        """
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a") as f:
            for _ in range(50):
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as e:
                    if e.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
                        raise
                    time.sleep(0.1)
            else:
                raise RuntimeError(f"could not acquire {self.lock_path.name} after 5s")
            try:
                yield
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def queue(self, text: str) -> None:
        """Append a notification. Safe from any process."""
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"ts": time.time(), "text": text}
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def recent(self, limit: int = 12) -> list[dict]:
        """Read-only tail of the log, newest LAST. Never touches the
        drain pointer — this feeds display surfaces (the dashboard's
        transmissions panel), not chat-context consumption."""
        if not self.log_path.exists():
            return []
        out: list[dict] = []
        try:
            with self.log_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(entry, dict) and entry.get("text"):
                        out.append(entry)
        except OSError:
            return []
        # Collapse the double-writes a notify() bug left in older logs
        # (same text queued twice within the same second).
        deduped: list[dict] = []
        for e in out:
            prev = deduped[-1] if deduped else None
            if (
                prev is not None
                and prev.get("text") == e.get("text")
                and abs(float(e.get("ts", 0)) - float(prev.get("ts", 0))) < 2.0
            ):
                continue
            deduped.append(e)
        return deduped[-max(1, limit):]

    def drain(self) -> list[dict]:
        """Return entries newer than the pointer; advance pointer atomically.

        Each entry: {"ts": float, "text": str}. Returns [] if empty or
        all consumed. Two concurrent drainers (telegram + web) are
        serialized by the flock — only one sees a given entry.
        """
        if not self.log_path.exists():
            return []
        with self._flock():
            last_ts = 0.0
            if self.pointer_path.exists():
                try:
                    last_ts = float(self.pointer_path.read_text(encoding="utf-8").strip())
                except (ValueError, OSError):
                    last_ts = 0.0
            fresh: list[dict] = []
            try:
                with self.log_path.open("r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(entry, dict):
                            continue
                        try:
                            ts = float(entry.get("ts", 0))
                        except (TypeError, ValueError):
                            continue
                        if ts > last_ts:
                            fresh.append(entry)
            except OSError:
                return []
            if not fresh:
                return []
            new_ts = max(float(e["ts"]) for e in fresh)
            tmp = self.pointer_path.with_suffix(".tmp")
            try:
                tmp.write_text(f"{new_ts}\n", encoding="utf-8")
                tmp.replace(self.pointer_path)
            except OSError:
                pass
            return fresh
