"""
Event log for the live thinking feed.

Every service (REPL / heartbeat / telegram) writes JSONL events to a
shared file at workspace/_events.jsonl. The feed service tails that
file and streams new lines to the browser over SSE.

Why a file: all three services already share the workspace volume.
No new infrastructure needed. tail -f works for debugging without the
UI. Survives feed-service downtime — services keep appending; the UI
catches up when it comes back.

Each line is a single JSON object with at minimum:
    ts:      ISO 8601 timestamp
    service: which homunculus process emitted it (repl/heartbeat/telegram)
    event:   short kind tag (user_message, tool_call, tool_result, assistant_reply)
plus event-specific fields.
"""

import json
import os
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, UTC
from pathlib import Path
from collections.abc import Iterator

from homunculus.security import redact_secrets


# Where the JSONL lives. Sits in workspace/ next to the agent's other
# runtime state. Single shared file across all services.
_EVENTS_PATH = Path(os.environ.get("HOMUNCULUS_EVENTS_PATH", "_events.jsonl"))

# Identifies which service is emitting. Set via env in docker-compose.yml
# so the feed UI can label each line. Falls back to "unknown" so missing
# env doesn't crash anything.
_SERVICE = os.environ.get("HOMUNCULUS_SERVICE", "unknown")


# Which task's run is executing on this thread, if any. Events are otherwise
# only ordered in time, and a heartbeat interleaves many tasks — so a metric
# that windows the log by timestamp alone attributes one task's tool calls to
# whichever task happens to bracket them. That is not a small error: a single
# reflection tick looping on one skill once landed 214 guard fires inside
# every other task's window.
_current_task: ContextVar[str] = ContextVar("homunculus_current_task", default="")


@contextmanager
def task_context(task_id: str) -> Iterator[None]:
    """Stamp every event emitted inside this block with `task_id`."""
    token = _current_task.set(str(task_id or ""))
    try:
        yield
    finally:
        _current_task.reset(token)


def emit(event: str, **fields) -> None:
    """Append one JSON event line to the shared events log.

    Best-effort: any write error is swallowed. We never want a logging
    failure to break the agent's actual work.
    """
    record = {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "service": _SERVICE,
        "event": event,
        **fields,
    }
    task_id = _current_task.get()
    if task_id and "task" not in record:
        record["task"] = task_id
    try:
        # Redact the serialized line rather than each field: tool args and
        # results nest arbitrarily, and one pass over the final text cannot be
        # bypassed by a credential buried a few levels down. The marker
        # substituted in carries no quotes or backslashes, so the line stays
        # valid JSON.
        line = redact_secrets(json.dumps(record, ensure_ascii=False))
        with _EVENTS_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        # Logging must never break the caller. Drop the event silently.
        pass


def rotate(keep_days: int = 14) -> int:
    """Trim _events.jsonl to the last `keep_days` days.

    Reads the file, drops lines older than the cutoff, rewrites in place.
    Returns the number of lines dropped. No-ops if the file doesn't exist
    or is already small. Call once at service startup.
    """
    if not _EVENTS_PATH.exists():
        return 0
    try:
        from datetime import timedelta
        cutoff = (datetime.now(UTC) - timedelta(days=keep_days)).isoformat(timespec="seconds")
        lines = _EVENTS_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
        kept: list[str] = []
        dropped = 0
        for i, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue
            try:
                ts = json.loads(line, strict=False).get("ts", "")
                if ts < cutoff:
                    dropped += 1
                    continue
            except Exception:
                # Malformed line: no consumer can read it (budget, stats,
                # and the feed all skip unparseable lines), so keeping it
                # only preserves corruption forever — a disk-full crash
                # once pinned a multi-MB NUL blob in the log this way.
                # Drop it, EXCEPT the final line, which may be another
                # service's append still in flight rather than corruption.
                if i < len(lines) - 1:
                    dropped += 1
                    continue
            kept.append(line)
        if dropped:
            _EVENTS_PATH.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
        return dropped
    except Exception:
        return 0


def truncate_preview(text: str, limit: int = 240) -> str:
    """Shorten a string for display in the feed. Multi-line → single line.

    Use a large limit for events where full content matters for observability
    (assistant_reply, user_message). Use the default for tool args/results
    which can be megabytes.
    """
    if text is None:
        return ""
    s = " ".join(str(text).split())
    if len(s) <= limit:
        return s
    return s[:limit] + f"… (+{len(s) - limit} chars)"


def full_text(text: str) -> str:
    """Store the full text for events where complete content is needed.

    assistant_reply and user_message should always be queryable in full
    from the traces UI. Tool results are intentionally capped via
    truncate_preview() since they can be megabytes.
    """
    if text is None:
        return ""
    # Collapse multi-line into single-line (JSON-safe) but keep full length.
    return " ".join(str(text).split())


def read_appended_records(path: Path, start_offset: int) -> tuple[list[dict], int]:
    """Parse the records appended to an event log since `start_offset`.

    Returns (records, next_offset). Only COMPLETE lines are consumed:
    next_offset stops at the last newline in the read, so a record another
    process is mid-append is never half-parsed — the next call picks it up
    whole. Unparseable lines are skipped (the log is best-effort by design).

    Rotation contract: the caller must detect a shrunken file
    (path.stat().st_size < start_offset) and restart from offset 0 with a
    fresh accumulator; this function assumes the log only grew.
    """
    try:
        with path.open("rb") as f:
            f.seek(start_offset)
            blob = f.read()
    except OSError:
        return [], start_offset
    last_nl = blob.rfind(b"\n")
    if last_nl == -1:
        return [], start_offset
    records: list[dict] = []
    for line in blob[: last_nl + 1].decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records, start_offset + last_nl + 1
