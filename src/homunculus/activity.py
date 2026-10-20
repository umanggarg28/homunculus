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

    def record(self, kind: str, **details: Any) -> None:
        entry = {"time": datetime.now(UTC).isoformat(), "kind": kind, **details}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a") as log:
            log.write(json.dumps(entry) + "\n")

    def entries(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        return [json.loads(line) for line in self._path.read_text().splitlines()]
