"""Append-only message transcript with stable IDs (Letta pattern).

Background
----------
Until now Agent.history did two jobs:
  1. The working set of messages sent to the LLM each turn
  2. The canonical record of what was said (persisted to _session.json)

Mixing the two means compaction — which rewrites the working set to a
summary + tail for token efficiency — also destroys the canonical
record. Heartbeat agents have no way to recover the pre-compaction
turns; the chat surface got a `_chat_log.jsonl` band-aid but that only
covered chat.

The Letta fix is to split storage from view. The transcript is the
forever-record: append-only, every message gets a stable ID, nothing is
ever mutated or deleted. The agent then holds a `message_ids` pointer
list — when compaction fires, only the pointer list is rewritten
(summary message appended to transcript, ids replaced with [summary_id,
*tail_ids]). The original turns stay on disk.

This module is the standalone storage layer — no agent integration yet.
PR #110 wires Agent to use it; PR #111 cuts over reads.

Format
------
JSONL on disk, one record per line:

    {"id": "000001", "ts": "2026-06-08T12:34:56", "msg": {...provider dict...}}

`id` is a zero-padded 6-digit sequence number, stable across restarts
because we resume from `max(existing) + 1` on load. The 6 digits give
us ~1M turns per agent before we'd need to widen — plenty of headroom.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from homunculus.locking import file_lock


class Transcript:
    """Append-only message store keyed by stable ID.

    One Transcript per Memory dir (one per agent). Safe for multiple
    processes via fcntl flock on a sidecar lock file — the web/telegram
    transports can each hold their own Memory instance pointing at the
    same dir.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        # _next_seq is computed lazily so __init__ is cheap and doesn't
        # touch disk. _ensure_seq() warms it before the first append.
        self._next_seq: int | None = None

    # ---- lifecycle ----------------------------------------------------

    def _resolve_next_seq_from_disk(self) -> None:
        """Scan the file and set `_next_seq` to max(existing) + 1.

        MUST be called inside _flock(). Single-instance writers can
        get away with caching the counter, but two Transcript objects
        pointing at the same file (e.g. web + telegram transports
        sharing a Memory dir) would collide. Re-resolving inside the
        lock makes the sequence cross-process safe at the cost of one
        file scan per append.
        """
        if not self.path.exists():
            self._next_seq = 1
            return
        max_seq = 0
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rid = rec.get("id")
                if isinstance(rid, str) and rid.isdigit():
                    seq = int(rid)
                    if seq > max_seq:
                        max_seq = seq
        self._next_seq = max_seq + 1

    @contextmanager
    def _flock(self) -> Iterator[None]:
        """Exclusive lock around an append. Thin wrapper over the canonical
        ``locking.file_lock`` so existing ``self._flock()`` call sites are
        unchanged."""
        with file_lock(self._lock_path):
            yield

    # ---- writes -------------------------------------------------------

    def append(self, msg: dict, ts: str | None = None) -> str:
        """Append `msg` and return its stable ID.

        `ts` defaults to ISO-formatted now() in naive local time — same
        shape as elsewhere in the codebase. Callers can pass an explicit
        `ts` when replaying an existing record (migration path).
        """
        if not isinstance(msg, dict):
            raise TypeError(f"transcript.append expects dict, got {type(msg).__name__}")
        with self._flock():
            self._resolve_next_seq_from_disk()
            assert self._next_seq is not None  # for type checker
            rid = f"{self._next_seq:06d}"
            self._next_seq += 1
            record = {
                "id": rid,
                "ts": ts or datetime.now().isoformat(timespec="seconds"),
                "msg": msg,
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # O_APPEND on POSIX makes the write atomic for small lines.
            # Combined with the flock above, two processes can't interleave.
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            return rid

    def append_many(self, msgs: list[dict]) -> list[str]:
        """Bulk-append; returns the list of assigned IDs in order.

        Cheaper than calling append() in a loop because we take the lock
        once. Used by the migration path that replays _session.json into
        a fresh transcript.
        """
        if not msgs:
            return []
        with self._flock():
            self._resolve_next_seq_from_disk()
            assert self._next_seq is not None
            ids: list[str] = []
            now_ts = datetime.now().isoformat(timespec="seconds")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                for msg in msgs:
                    if not isinstance(msg, dict):
                        raise TypeError(
                            f"transcript.append_many entry must be dict, got {type(msg).__name__}",
                        )
                    rid = f"{self._next_seq:06d}"
                    self._next_seq += 1
                    record = {"id": rid, "ts": now_ts, "msg": msg}
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
                    ids.append(rid)
            return ids

    # ---- reads --------------------------------------------------------

    def _iter_records(self) -> Iterator[dict]:
        """Yield every record dict on disk, skipping malformed lines.

        Read path is lock-free — append-only means readers see a
        consistent prefix. A partially-flushed final line is dropped by
        the json.loads guard, same approach as load_chat_log.
        """
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (
                    isinstance(rec, dict)
                    and isinstance(rec.get("id"), str)
                    and isinstance(rec.get("msg"), dict)
                ):
                    yield rec

    def get(self, msg_id: str) -> dict | None:
        """Return the message dict for `msg_id`, or None if not present.

        Linear scan today. The transcript is bounded by the user's actual
        usage — even an extremely chatty user produces <10k records — and
        every Agent.history materialization is sequential anyway, so an
        index isn't load-bearing yet. Revisit if a future profile shows
        get() as hot.
        """
        for rec in self._iter_records():
            if rec["id"] == msg_id:
                return rec["msg"]
        return None

    def get_many(self, msg_ids: list[str]) -> list[dict]:
        """Return the messages for `msg_ids`, in the order requested.

        Missing IDs are silently dropped — the pointer list may reference
        an ID that was on disk last run but is now gone (e.g. the file
        was manually truncated). Skipping rather than raising means a
        damaged transcript degrades to "shorter history" instead of
        crashing the agent on startup.
        """
        if not msg_ids:
            return []
        wanted = set(msg_ids)
        found: dict[str, dict] = {}
        for rec in self._iter_records():
            if rec["id"] in wanted:
                found[rec["id"]] = rec["msg"]
                if len(found) == len(wanted):
                    break
        return [found[i] for i in msg_ids if i in found]

    def all_ids(self) -> list[str]:
        """Every ID on disk, in append order."""
        return [rec["id"] for rec in self._iter_records()]

    def all(self) -> list[tuple[str, dict]]:
        """Every (id, msg) on disk, in append order. Used by chat
        history endpoint after the cutover."""
        return [(rec["id"], rec["msg"]) for rec in self._iter_records()]

    def __len__(self) -> int:
        count = 0
        for _ in self._iter_records():
            count += 1
        return count

    # ---- maintenance --------------------------------------------------

    def clear(self) -> None:
        """Delete the transcript file. Called on reset / chapter close."""
        with self._flock():
            if self.path.exists():
                self.path.unlink()
            self._next_seq = 1
