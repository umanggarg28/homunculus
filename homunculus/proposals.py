"""Proposal store — the review queue for agent-authored skills.

When the agent wants to change its own behavior — refine a failing skill
(#1) or learn a new job from a chat request (#2) — it does NOT write the
skill directly. It files a *proposal* here. Proposals carry the
validated body, a rationale, and (for new skills) the task that should
run them. They sit in `pending` until a human approves or rejects them
in the containment UI; only on approval does the change reach the live
skill registry.

This is the containment thesis applied to the agent's own mind: the most
dangerous thing an autonomous agent can do is rewrite itself, so every
self-modification passes a deterministic validation gate AND a human
gate before it goes live, and every approved change is versioned and
reversible via Skills.save's history.

Mirrors TaskStore / QuizStore: one JSON file, atomic writes, pure-data
methods, corrupt-file recovery.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

# kind: what the proposal does on approval.
KIND_NEW_SKILL = "new_skill"    # create skill (+ optional task) — chat "teach it a job"
KIND_SKILL_EDIT = "skill_edit"  # replace an existing skill body — failure-driven refinement

_STATUSES = {"pending", "approved", "rejected"}


def _now() -> str:
    from homunculus.user_tz import now_user_naive
    return now_user_naive().isoformat(timespec="seconds")


class ProposalStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    # --- persistence -----------------------------------------------------

    def _load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []  # corrupt — recover as empty rather than crash the agent
        return data if isinstance(data, list) else []

    def _save(self, data: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def _next_id(self, data: list[dict]) -> str:
        n = 0
        for p in data:
            pid = str(p.get("id", ""))
            if pid.startswith("prop-"):
                try:
                    n = max(n, int(pid[5:]))
                except ValueError:
                    pass
        return f"prop-{n + 1:04d}"

    # --- write side ------------------------------------------------------

    def create(
        self,
        *,
        kind: str,
        skill_name: str,
        body: str,
        rationale: str = "",
        source: str = "",
        task_spec: dict | None = None,
        validation: dict | None = None,
    ) -> dict[str, Any]:
        if kind not in (KIND_NEW_SKILL, KIND_SKILL_EDIT):
            raise ValueError(f"unknown proposal kind: {kind!r}")
        data = self._load()
        proposal = {
            "id": self._next_id(data),
            "kind": kind,
            "skill_name": skill_name,
            "body": body,
            "rationale": (rationale or "").strip()[:1000],
            "source": source,
            "task_spec": task_spec,
            "validation": validation or {},
            "status": "pending",
            "created_at": _now(),
            "resolved_at": None,
            "resolution_note": "",
        }
        data.append(proposal)
        self._save(data)
        return proposal

    def _set_status(self, proposal_id: str, status: str, note: str = "") -> dict[str, Any]:
        if status not in _STATUSES:
            raise ValueError(f"unknown status: {status!r}")
        data = self._load()
        for p in data:
            if p.get("id") == proposal_id:
                if p.get("status") != "pending":
                    raise ValueError(
                        f"proposal {proposal_id} is already {p['status']}; "
                        "only pending proposals can be resolved"
                    )
                p["status"] = status
                p["resolved_at"] = _now()
                p["resolution_note"] = (note or "").strip()[:500]
                self._save(data)
                return p
        raise KeyError(f"proposal {proposal_id} not found")

    def mark_approved(self, proposal_id: str, note: str = "") -> dict[str, Any]:
        return self._set_status(proposal_id, "approved", note)

    def mark_rejected(self, proposal_id: str, note: str = "") -> dict[str, Any]:
        return self._set_status(proposal_id, "rejected", note)

    # --- read side -------------------------------------------------------

    def get(self, proposal_id: str) -> dict[str, Any] | None:
        return next((p for p in self._load() if p.get("id") == proposal_id), None)

    def list(self, status: str | None = "pending") -> list[dict[str, Any]]:
        data = self._load()
        if status is None or status == "all":
            return data
        return [p for p in data if p.get("status") == status]

    def pending_count(self) -> int:
        return len(self.list("pending"))


def _store() -> ProposalStore:
    return ProposalStore(Path(os.environ.get("HOMUNCULUS_PROPOSALS_FILE", "./proposals.json")))
