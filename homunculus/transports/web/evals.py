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
            # A total, not an average: see RunScore.reply_blocks. The UI shows
            # it only when non-zero, so a clean scorecard stays uncluttered and
            # a single occurrence is impossible to miss.
            "reply_blocks": card.reply_blocks,
            "avg_cost_cents": (
                round(card.avg_cost_cents, 4) if card.avg_cost_cents is not None else None
            ),
            "trend": card.trend,
            # Keyed by model_id ("" = recorded before model-tracking
            # shipped, rendered "unknown"). One entry per model a run
            # ever executed under — a swap shows up as two slices to
            # compare rather than one blended average.
            "by_model": {
                (model or "unknown"): {
                    "runs": slice_.runs,
                    "compliance_rate": slice_.compliance_rate,
                    "avg_violations": slice_.avg_violations,
                    "avg_guard_fires": slice_.avg_guard_fires,
                    "reply_blocks": slice_.reply_blocks,
                    "avg_cost_cents": (
                        round(slice_.avg_cost_cents, 4)
                        if slice_.avg_cost_cents is not None else None
                    ),
                }
                for model, slice_ in card.by_model.items()
            },
        }
        for task_id, card in scorecards.items()
    })
