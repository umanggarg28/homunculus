"""The activity log: an append-only record of what the agent did, written by code.

It lives outside the workspace, so no tool can read or change it.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class ActivityLog:
    def __init__(self, path: Path) -> None:
        self._path = path

    def record(self, kind: str, **details: Any) -> dict[str, Any]:
        """Add one line to the log and return it."""
        entry = {"time": datetime.now(UTC).isoformat(), "kind": kind, **details}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a") as log:
            log.write(json.dumps(entry) + "\n")
        return entry

    def entries(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        return [json.loads(line) for line in self._path.read_text().splitlines()]


def jobs(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group log entries into jobs, newest first.

    A job is one turn: the message that started it, each tool step, and the reply or
    the error that ended it, plus the latest feedback the operator gave on it.
    Entries without a job, such as the operator's decisions, are not part of one. Nor
    are lines written before the log carried job ids, so those are not shown.
    """
    by_id: dict[str, dict[str, Any]] = {}
    for entry in entries:
        job_id = entry.get("job")
        if job_id is None:
            continue
        kind = entry["kind"]
        if kind == "message":
            by_id[job_id] = {
                "job": job_id,
                "started": entry["time"],
                "ended": None,
                "message": entry["text"],
                "notice": bool(entry.get("notice")),
                "steps": [],
                "reply": None,
                "error": None,
                "feedback": None,
            }
            continue
        job = by_id.get(job_id)
        if job is None:
            continue  # feedback or a step naming a job this log has no message for
        # An approved action runs after its job has ended, so steps are added either way.
        if kind == "tool":
            keys = ("name", "arguments", "answer", "result", "outcome", "request")
            step = {k: entry.get(k) for k in keys}
            job["steps"].append({"time": entry["time"], **step})
        elif kind == "reply":
            job["reply"], job["ended"] = entry["text"], entry["time"]
        elif kind == "error":
            job["error"], job["ended"] = entry["error"], entry["time"]
        elif kind == "feedback":
            job["feedback"] = {
                "rating": entry["rating"],
                "reason": entry.get("reason"),
                "time": entry["time"],
            }
    return list(reversed(by_id.values()))
