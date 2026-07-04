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


@router.post("/api/proposals/approve-batch", dependencies=[Depends(wa.require_web_auth)])
async def proposals_approve_batch(request: Request) -> JSONResponse:
    """Approve several pending proposals in one action. Body: {ids: [...]}.

    Each id goes through the SAME by-id resolver as a single approval —
    the batch is a UI convenience (a consolidation scan files five
    near-identical memory deletions; five clicks is ceremony), never a
    second code path. Per-id failures don't abort the rest: the caller
    gets an outcome per id.
    """
    from homunculus.approvals import ProposalError, resolve_proposal

    body = await request.json() if await request.body() else {}
    ids = [str(i) for i in (body or {}).get("ids", []) if i]
    if not ids or len(ids) > 20:
        raise HTTPException(400, "pass 1-20 proposal ids")

    results: list[dict] = []
    for pid in ids:
        try:
            res = resolve_proposal(
                pid, "approve",
                memory_dir=wa.MEMORY_DIR, tasks_dir=wa.TASKS_DIR,
                store=wa._proposal_store(), known_tools=wa._known_tool_names(),
            )
            results.append({"id": pid, "ok": True, "detail": res.detail})
        except ProposalError as e:
            results.append({"id": pid, "ok": False, "error": e.message})
    return JSONResponse({
        "approved": sum(1 for r in results if r["ok"]),
        "failed": sum(1 for r in results if not r["ok"]),
        "results": results,
    })
