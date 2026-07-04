"""Memory routes — list/read/edit/delete entries, consolidation, chapters."""

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from homunculus.memory import Memory
from homunculus.transports import web_api as wa

router = APIRouter()


@router.get("/api/memory", dependencies=[Depends(wa.require_web_auth)])
def memory_list() -> JSONResponse:
    return JSONResponse(wa._list_memory_entries())


@router.get(
    "/api/memory/{filename}/raw",
    response_class=PlainTextResponse,
    dependencies=[Depends(wa.require_web_auth)],
)
def memory_entry_raw(filename: str) -> PlainTextResponse:
    safe = wa._safe_subpath(filename, wa.MEMORY_DIR)
    if safe is None or not safe.exists() or not safe.is_file():
        raise HTTPException(404, "Memory entry not found")
    return PlainTextResponse(safe.read_text(encoding="utf-8"))


@router.put(
    "/api/memory/{filename}/raw",
    dependencies=[Depends(wa.require_web_auth)],
)
async def memory_entry_update(filename: str, request: Request) -> JSONResponse:
    """Overwrite a memory entry's raw markdown. Used by inline edit."""
    safe = wa._safe_subpath(filename, wa.MEMORY_DIR)
    if safe is None:
        raise HTTPException(400, "invalid path")
    if not safe.exists() or not safe.is_file():
        raise HTTPException(404, "Memory entry not found")
    body = (await request.body()).decode("utf-8")
    # Refuse zero-length writes; the agent or user should use delete instead.
    if not body.strip():
        raise HTTPException(400, "empty body — use DELETE to remove an entry")
    safe.write_text(body, encoding="utf-8")
    return JSONResponse({"ok": True, "bytes": len(body.encode("utf-8"))})


@router.delete(
    "/api/memory/{filename}",
    dependencies=[Depends(wa.require_web_auth)],
)
def memory_entry_delete(filename: str) -> JSONResponse:
    """Remove a memory entry — file + MEMORY.md index pointer."""
    memory = wa._chat_memory or Memory(wa.MEMORY_DIR)
    result = memory.forget(filename)
    if "not found" in result.lower():
        raise HTTPException(404, result)
    return JSONResponse({"ok": True, "message": result})


@router.post("/api/memory/consolidation/propose", dependencies=[Depends(wa.require_web_auth)])
def memory_consolidation_propose(limit: int = 5) -> JSONResponse:
    """File human-gated memory hygiene proposals.

    Deterministic scan only: no LLM call, no embedding call, no direct memory
    mutation. The operator still approves/rejects every proposed deletion.
    """
    from homunculus.memory_consolidation import propose_consolidation
    from homunculus.proposals import KIND_MEMORY_DELETE, ProposalStore, proposals_path

    proposals = propose_consolidation(
        memory_root=wa.MEMORY_DIR,
        proposals_path=proposals_path(),
        limit=max(1, min(int(limit or 5), 20)),
    )
    # "0 filed" reads as "scan broken" when the real story is "candidates
    # exist but were deduped against proposals still awaiting review" —
    # report the pending count so the UI can say which one happened.
    pending = sum(
        1 for p in ProposalStore(proposals_path()).list("pending")
        if p.get("kind") == KIND_MEMORY_DELETE
    )
    return JSONResponse({"ok": True, "created": proposals, "pending": pending})


@router.get("/api/chapters", dependencies=[Depends(wa.require_web_auth)])
def chapters_list() -> JSONResponse:
    if not wa.CHAPTERS_DIR.exists():
        return JSONResponse([])
    items = []
    for p in sorted(wa.CHAPTERS_DIR.glob("*.json"), reverse=True):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        items.append({
            "id": p.stem,
            "title": data.get("title", p.stem),
            "opened_at": data.get("opened_at"),
            "closed_at": data.get("closed_at"),
            "turns": len(data.get("messages", [])),
        })
    return JSONResponse(items)


@router.post("/api/chapters/close", dependencies=[Depends(wa.require_web_auth)])
def chapter_close() -> JSONResponse:
    """Archive the open chapter and start a fresh session.

    Title is derived from the first user message of the session, or the
    closing timestamp if none. The agent's living `history` is reset so
    the next reply starts from a clean slate; long-term memory is unaffected.
    """
    memory = wa._chat_memory or Memory(wa.MEMORY_DIR)
    history = memory.load_session()
    if not history:
        raise HTTPException(400, "No open chapter to close.")

    wa.CHAPTERS_DIR.mkdir(parents=True, exist_ok=True)
    closed_at = datetime.now().isoformat(timespec="seconds")
    chapter_id = closed_at.replace(":", "-")

    first_user = next(
        (m.get("content", "") for m in history if m.get("role") == "user"),
        "",
    )
    title = first_user.strip().split("\n", 1)[0][:80] or f"chapter of {closed_at[:10]}"

    payload = {
        "id": chapter_id,
        "title": title,
        "opened_at": history[0].get("ts") if history else closed_at,
        "closed_at": closed_at,
        "messages": history,
    }
    (wa.CHAPTERS_DIR / f"{chapter_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    memory.clear_session()
    memory.clear_transcript()
    if wa._chat_agent is not None:
        wa._chat_agent.reset()

    return JSONResponse({"ok": True, "id": chapter_id, "title": title})
