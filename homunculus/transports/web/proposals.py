"""Proposal routes — list, and the human-gated approve/reject surface.

Approve/reject delegate to the shared `resolve_proposal`, so the dashboard and
the chat `approve <id>` command apply proposals through one validated path.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from homunculus.transports import web_api as wa

router = APIRouter()


@router.get("/api/proposals", dependencies=[Depends(wa.require_web_auth)])
def proposals_list(status: str = "pending") -> JSONResponse:
    """List skill proposals. status = pending | approved | rejected | all."""
    return JSONResponse(wa._proposal_store().list(status))


@router.post("/api/proposals/{proposal_id}/approve", dependencies=[Depends(wa.require_web_auth)])
def proposals_approve(proposal_id: str) -> JSONResponse:
    """Approve a pending proposal: re-validate against the live tool catalogue,
    commit the skill (versioned), and create any bundled task. Delegates to the
    shared resolver so the dashboard and chat commands apply proposals through
    one validated path."""
    from homunculus.approvals import ProposalError, resolve_proposal

    try:
        res = resolve_proposal(
            proposal_id, "approve",
            memory_dir=wa.MEMORY_DIR, tasks_dir=wa.TASKS_DIR,
            store=wa._proposal_store(), known_tools=wa._known_tool_names(),
        )
    except ProposalError as e:
        raise HTTPException(e.code, e.message) from None
    return JSONResponse(res.detail)


@router.post("/api/proposals/{proposal_id}/reject", dependencies=[Depends(wa.require_web_auth)])
async def proposals_reject(proposal_id: str, request: Request) -> JSONResponse:
    """Reject a pending proposal. Body: {reason}."""
    from homunculus.approvals import ProposalError, resolve_proposal

    body = await request.json() if await request.body() else {}
    try:
        res = resolve_proposal(
            proposal_id, "reject",
            memory_dir=wa.MEMORY_DIR, tasks_dir=wa.TASKS_DIR,
            store=wa._proposal_store(), reason=(body or {}).get("reason", ""),
        )
    except ProposalError as e:
        raise HTTPException(e.code, e.message) from None
    return JSONResponse(res.detail)
