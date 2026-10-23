"""Actions waiting for the operator: approved, declined, or done by the operator themselves.

Each request records the exact tool and arguments the agent proposed. Deciding a
request never changes them, so what the operator approves is exactly what runs.
Requests are kept in a file in the data directory, so a restart loses none of them.

One server process owns the file, and every change runs to completion without
awaiting, so two changes never interleave.
"""

import json
import os
import secrets
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

# "ask": the operator approves or declines. "hand off": the operator does it themselves.
Kind = Literal["ask", "hand off"]
Status = Literal["pending", "approved", "denied", "done"]

INTERRUPTED = (
    "Unknown: the server stopped while this action was running, so it may or may not have run."
)


class ApprovalError(Exception):
    """A request does not exist or has already been decided."""


@dataclass(frozen=True)
class Approval:
    id: str
    tool: str
    arguments: str
    kind: Kind
    status: Status
    created: str
    decided: str | None = None
    note: str | None = None  # the operator's reason or note, if they gave one
    result: str | None = None  # what the tool returned, when an approved action ran
    # Whether the agent has been told the outcome: "replied", or "failed: <why>".
    follow_up: str | None = None


class Approvals:
    def __init__(self, path: Path) -> None:
        self._path = path

    def request(self, tool: str, arguments: str, kind: Kind) -> Approval:
        requests = self.all()
        taken = {a.id for a in requests}
        new_id = secrets.token_hex(3)
        while new_id in taken:
            new_id = secrets.token_hex(3)
        approval = Approval(
            id=new_id, tool=tool, arguments=arguments, kind=kind, status="pending", created=_now()
        )
        self._save([*requests, approval])
        return approval

    def all(self) -> list[Approval]:
        """Every request, oldest first."""
        if not self._path.exists():
            return []
        return [Approval(**item) for item in json.loads(self._path.read_text())]

    def get(self, approval_id: str) -> Approval:
        for approval in self.all():
            if approval.id == approval_id:
                return approval
        raise ApprovalError(f"there is no request {approval_id}")

    def decide(self, approval_id: str, status: Status, note: str | None = None) -> Approval:
        """Record the operator's decision on a pending request."""
        if (current := self.get(approval_id)).status != "pending":
            raise ApprovalError(f"request {approval_id} was already {current.status}")
        return self._update(approval_id, status=status, decided=_now(), note=note)

    def record_result(self, approval_id: str, result: str) -> Approval:
        return self._update(approval_id, result=result)

    def record_follow_up(self, approval_id: str, follow_up: str) -> Approval:
        return self._update(approval_id, follow_up=follow_up)

    def mark_interrupted(self) -> None:
        """Mark approved actions that never recorded a result, because the server stopped
        while they ran. Whether they ran cannot be known, and the result says so."""
        for approval in self.all():
            if approval.status == "approved" and approval.result is None:
                self._update(approval.id, result=INTERRUPTED)

    def _update(self, approval_id: str, **changes: str | None) -> Approval:
        requests = self.all()
        for i, approval in enumerate(requests):
            if approval.id == approval_id:
                requests[i] = replace(approval, **changes)  # type: ignore[arg-type]
                self._save(requests)
                return requests[i]
        raise ApprovalError(f"there is no request {approval_id}")

    def _save(self, requests: list[Approval]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Written in full to a temporary file, flushed to disk, then moved into place,
        # so the file on disk is always either the old list or the new one.
        temporary = self._path.with_name(f".{self._path.name}.{secrets.token_hex(4)}.tmp")
        with temporary.open("w") as file:
            file.write(json.dumps([asdict(a) for a in requests], indent=2))
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(self._path)


def _now() -> str:
    return datetime.now(UTC).isoformat()
