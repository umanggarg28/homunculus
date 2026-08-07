"""Evals routes — trajectory scorecards for the Evals page.

Thin adapter over homunculus.evals.score_all: no scoring logic lives
here, only reading the task store + event log and shaping the JSON the
frontend renders. Same "harness computes, page only displays" contract
as dashboard.py's stats endpoints.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from homunculus.evals import load_events, score_all
from homunculus.transports import web_api as wa

router = APIRouter()


@router.get("/api/evals", dependencies=[Depends(wa.require_web_auth)])
def evals_scorecards() -> JSONResponse:
    tasks = wa._task_store().list("all")
    events = load_events(wa.EVENTS_PATH)
    scorecards = score_all(tasks, events, wa.MEMORY_DIR)

    return JSONResponse({
        task_id: {
            "contract_kind": card.contract_kind,
            "runs": card.runs,
            "compliance_rate": card.compliance_rate,
            "avg_violations": card.avg_violations,
            "avg_guard_fires": card.avg_guard_fires,
            "avg_cost_cents": (
                round(card.avg_cost_cents, 4) if card.avg_cost_cents is not None else None
            ),
            "trend": card.trend,
        }
        for task_id, card in scorecards.items()
    })
