"""Single source of truth for resolving a human-gated proposal.

Approving or rejecting a proposal is the human half of the containment gate:
the agent files skill/memory changes as proposals (proposals.py) and they stay
inert until a person resolves them. That resolution must behave identically no
matter which surface it arrives from — the web dashboard or a chat command — so
the apply logic lives here, not inside any one transport.

Pattern lineage: resolution is a single by-id operation that bypasses the model
— Letta surfaces an ApprovalRequestMessage resolved by tool_call_id, LangGraph
resumes an interrupt with Command(resume=...), OpenClaw routes the command to an
authorized-sender handler. Each surface translates the ResolveResult, or the
raised ProposalError, into its own response shape.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from homunculus.proposals import (
    KIND_MEMORY_DELETE,
    KIND_NEW_SKILL,
    ProposalStore,
    proposals_path,
)

_PROTECTED = {"MEMORY.md", "README.md"}


class ProposalError(Exception):
    """Resolution could not proceed. ``code`` mirrors the HTTP status the web
    surface returns (404 missing, 409 already resolved, 422/400 invalid) so
    every surface translates the failure consistently."""

    def __init__(self, message: str, *, code: int) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass(frozen=True)
class ResolveResult:
    ok: bool
    proposal_id: str
    kind: str
    action: str  # applied | deleted | rejected
    summary: str  # one-line, human-readable (chat + feed)
    detail: dict[str, Any]  # full payload — byte-parity with the dashboard JSON


def _safe_under(rel: str, root: Path) -> Path | None:
    """Resolve ``rel`` under ``root``, rejecting path traversal."""
    try:
        candidate = (root / rel).resolve()
        root_resolved = root.resolve()
    except (OSError, ValueError):
        return None
    try:
        candidate.relative_to(root_resolved)
    except ValueError:
        return None
    return candidate


def known_tool_names() -> set[str]:
    """Live tool-catalogue names, read from the static tool schemas. Imported
    lazily so resolving a proposal never forces MCP startup in a process (e.g.
    tests) that passes ``known_tools`` explicitly."""
    from homunculus import tools

    names: set[str] = set()
    for s in getattr(tools, "SCHEMAS", []) or []:
        fn = s.get("function") if isinstance(s, dict) else None
        name = (fn or {}).get("name") if fn else (s.get("name") if isinstance(s, dict) else None)
        if name:
            names.add(name)
    return names


def resolve_proposal(
    proposal_id: str,
    action: str,
    *,
    memory_dir: Path,
    tasks_dir: Path,
    store: ProposalStore | None = None,
    known_tools: set[str] | None = None,
    reason: str = "",
) -> ResolveResult:
    """Approve or reject one pending proposal — the ONLY place a proposal is
    applied. Raises ProposalError(code=...) on any failure; returns a
    ResolveResult on success. Resolution is idempotent-by-id: a proposal that is
    no longer pending raises 409 rather than applying twice."""
    if action not in {"approve", "reject"}:
        raise ProposalError(f"unknown action: {action!r}", code=400)

    store = store or ProposalStore(proposals_path())
    p = store.get(proposal_id)
    if p is None:
        raise ProposalError(f"proposal {proposal_id} not found", code=404)
    if p.get("status") != "pending":
        raise ProposalError(f"proposal is already {p.get('status')}", code=409)

    kind = str(p.get("kind", ""))

    def _emit_resolved(status: str) -> None:
        # Push a lifecycle event so the web Overview refetches its proposal
        # queue immediately — without it, a resolution from another channel
        # (Discord/Telegram approve) only shows up on the panel's 30s poll.
        try:
            import homunculus.events as _events
            _events.emit("proposal_resolved", name=proposal_id, result=status, text=kind)
        except Exception:
            pass

    if action == "reject":
        store.mark_rejected(proposal_id, note=reason)
        _emit_resolved("rejected")
        return ResolveResult(
            ok=True, proposal_id=proposal_id, kind=kind, action="rejected",
            summary=f"Rejected {proposal_id}" + (f" — {reason}" if reason else ""),
            detail={"ok": True, "id": proposal_id, "status": "rejected"},
        )

    # ---- approve: memory_delete ----
    if kind == KIND_MEMORY_DELETE:
        from homunculus.memory import Memory

        target = str((p.get("validation") or {}).get("target") or p.get("skill_name") or "")
        safe = _safe_under(target, memory_dir)
        if safe is None or safe.suffix != ".md" or safe.name in _PROTECTED:
            raise ProposalError(f"invalid memory proposal target: {target!r}", code=400)
        result = Memory(memory_dir).forget(safe.name)
        store.mark_approved(proposal_id, note=result[:500])
        _emit_resolved("approved")
        return ResolveResult(
            ok=True, proposal_id=proposal_id, kind=kind, action="deleted",
            summary=f"Deleted memory {safe.name}",
            detail={"ok": True, "kind": kind, "memory": safe.name,
                    "action": "deleted", "message": result},
        )

    # ---- approve: new_skill / skill_edit ----
    from homunculus.skills import Skills
    from homunculus.skill_validation import validate_skill_body
    from homunculus.tasks import TaskStore

    tools_set = known_tools if known_tools is not None else known_tool_names()
    valid = validate_skill_body(p["body"], expected_name=p["skill_name"], known_tools=tools_set)
    if not valid.ok:
        raise ProposalError("proposal no longer valid: " + "; ".join(valid.errors), code=422)

    version = Skills(memory_dir).save(
        p["skill_name"], p["body"],
        source="proposal-approved",
        rationale=p.get("rationale", "")[:500],
    )

    created_task = None
    spec = p.get("task_spec")
    if kind == KIND_NEW_SKILL and spec:
        try:
            created_task = TaskStore(tasks_dir).create(
                title=spec["title"],
                description=spec.get("description", f"Runs {p['skill_name']} — see the skill playbook."),
                due_at=spec.get("due_at"),
                recurrence=spec.get("recurrence", "none"),
                notify=spec.get("notify", True),
                success_criteria=spec.get("success_criteria"),
                skill=p["skill_name"],
            )
        except (ValueError, KeyError) as e:
            raise ProposalError(
                f"skill saved (v{version}) but task creation failed: {e}", code=400
            ) from e

    store.mark_approved(proposal_id, note=f"applied as v{version}")
    _emit_resolved("approved")

    warning = None
    if created_task is None:
        linked = any((t.get("skill") == p["skill_name"]) for t in TaskStore(tasks_dir).all())
        if not linked:
            warning = (
                f"Skill {p['skill_name']} is approved but NO task is linked to it — "
                f"it won't run until you link a task (set its skill to {p['skill_name']})."
            )

    return ResolveResult(
        ok=True, proposal_id=proposal_id, kind=kind, action="applied",
        summary=f"Approved {p['skill_name']} (v{version})",
        detail={"ok": True, "skill": p["skill_name"], "version": version,
                "task": created_task, "warning": warning},
    )


# ── chat command surface ──────────────────────────────────────────────────
# Resolving a proposal from a chat reply ("approve prop-0021") is the same
# by-id operation as the dashboard button — OpenClaw routes such a command to
# an authorized-sender handler that bypasses the model. The transports MUST
# call this only AFTER their owner check (single configured user), so a group
# member can never approve. No LLM, no budget.

_APPROVAL_CMD = re.compile(r"^\s*(approve|reject)\s+(prop-\d+)\b\s*(.*)$",
                           re.IGNORECASE | re.DOTALL)


def parse_approval_command(text: str) -> tuple[str, str, str] | None:
    """Parse 'approve prop-0021' / 'reject prop-0021 <reason>'. Returns
    (action, proposal_id, reason) with a lowercased id, or None if the text is
    not an approval command (so the caller routes it to the agent instead)."""
    m = _APPROVAL_CMD.match(text or "")
    if not m:
        return None
    return m.group(1).lower(), m.group(2).lower(), m.group(3).strip()


# ── surfacing a newly-filed proposal ──────────────────────────────────────
# A proposal exists to be approved, so the user must learn it's waiting — the
# moment it's filed, from whatever source (autonomous tick, the Memory-page scan
# button, chat "teach it a skill"). Letta emits its ApprovalRequestMessage when
# the approval is created, not from a scheduler; we surface here, called from the
# creation funnels (propose_skill / propose_consolidation), so it can never be
# path-dependent. The agent never narrates this itself — it's harness-owned and
# structured.

_KIND_LABELS = {
    "new_skill": "NEW SKILL",
    "skill_edit": "EDIT SKILL",
    "memory_delete": "DELETE MEMORY",
}


def format_approval_notice(p: dict[str, Any]) -> str:
    label = _KIND_LABELS.get(str(p.get("kind", "")), "PROPOSAL")
    target = str(p.get("skill_name", "")).strip()
    pid = str(p.get("id", ""))
    first_line = (str(p.get("rationale", "")).strip().splitlines() or [""])[0]
    lines = [f"🔔 Approval needed — {label}: {target}"]
    if first_line:
        lines.append(first_line[:200])
    lines.append(f"Review in Overview · reply `approve {pid}` or `reject {pid} <reason>`")
    return "\n".join(lines)


def announce_proposal(proposal: dict[str, Any]) -> None:
    """Surface a newly-filed proposal to the user's chat channels so they can
    approve it remotely. Best-effort: a delivery failure must never fail the
    proposal-creation it follows. Skips deduped re-files (already pending)."""
    if not proposal or proposal.get("_deduped"):
        return
    try:
        from homunculus.tools.notify import deliver
        deliver(format_approval_notice(proposal))
    except Exception:
        pass


def try_resolve_from_chat(text: str) -> str | None:
    """If ``text`` is an approve/reject command, resolve the proposal and return
    a reply string; otherwise None so the caller routes the message to the
    agent. Callers MUST gate this behind the transport's owner check."""
    parsed = parse_approval_command(text)
    if parsed is None:
        return None
    action, proposal_id, reason = parsed
    memory_dir = Path(os.environ.get("HOMUNCULUS_MEMORY_DIR", "./memory"))
    tasks_dir = Path(os.environ.get("HOMUNCULUS_TASKS_DIR", "./tasks"))
    try:
        res = resolve_proposal(
            proposal_id, action, memory_dir=memory_dir, tasks_dir=tasks_dir, reason=reason
        )
    except ProposalError as e:
        return f"⚠️ {e.message}"
    reply = f"✅ {res.summary}"
    if res.detail.get("warning"):
        reply += f"\n⚠️ {res.detail['warning']}"
    return reply
