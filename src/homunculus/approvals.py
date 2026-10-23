"""Actions waiting for the operator: approved, declined, or done by the operator themselves.

Each request records the exact tool and arguments the agent proposed. Deciding a
request never changes them, so what the operator approves is exactly what runs.
Requests are kept in a file in the data directory, so a restart loses none of them.
"""

import json
import secrets
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path


class ApprovalError(Exception):
    """A request does not exist or has already been decided."""


@dataclass(frozen=True)
class Approval:
    id: str
    tool: str
    arguments: str
    kind: str  # "ask": approve or deny; "hand off": the operator does it themselves
    status: str  # "pending", "approved", "denied" or "done"
    created: str
    decided: str | None = None
    note: str | None = None  # the operator's reason or note, if they gave one
    result: str | None = None  # what the tool returned, when an approved action ran


class Approvals:
    def __init__(self, path: Path) -> None:
        self._path = path

    def request(self, tool: str, arguments: str, kind: str) -> Approval:
        approval = Approval(
            id=secrets.token_hex(3),
            tool=tool,
            arguments=arguments,
            kind=kind,
            status="pending",
            created=_now(),
        )
        self._save([*self.all(), approval])
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

    def pending(self) -> list[Approval]:
        return [a for a in self.all() if a.status == "pending"]

    def decide(self, approval_id: str, status: str, note: str | None = None) -> Approval:
        """Record the operator's decision on a pending request."""
        requests = self.all()
        for i, approval in enumerate(requests):
            if approval.id == approval_id:
                if approval.status != "pending":
                    raise ApprovalError(f"request {approval_id} was already {approval.status}")
                requests[i] = replace(approval, status=status, decided=_now(), note=note)
                self._save(requests)
                return requests[i]
        raise ApprovalError(f"there is no request {approval_id}")

    def record_result(self, approval_id: str, result: str) -> Approval:
        requests = self.all()
        for i, approval in enumerate(requests):
            if approval.id == approval_id:
                requests[i] = replace(approval, result=result)
                self._save(requests)
                return requests[i]
        raise ApprovalError(f"there is no request {approval_id}")

    def _save(self, requests: list[Approval]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Written to a temporary file and moved into place, so a crash mid-write
        # can never leave a half-written list.
        temporary = self._path.with_suffix(".tmp")
        temporary.write_text(json.dumps([asdict(a) for a in requests], indent=2))
        temporary.replace(self._path)


def _now() -> str:
    return datetime.now(UTC).isoformat()
