"""week_in_review — the agent's own inspection report, computed by the harness.

Same contract as task_health_summary: the tool returns pre-computed
JSON that is the ONLY source of truth, and the model's job is to format
it into a readable message — never to recount events or re-read raw
logs (a weak model summarising 7 days of JSONL invents numbers).

Counting lives in stats.py, shared with /api/stats/today, so the report
the agent sends and the dashboard the operator reads can never
disagree.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, UTC

from homunculus import stats
from homunculus.user_tz import get_user_tz

from .scheduling import _task_store


def week_in_review() -> str:
    tz = get_user_tz() or UTC
    now_local = datetime.now(tz)
    since_local = now_local - timedelta(days=7)

    s = stats.summarize_events(since_local.astimezone(UTC), tz=tz)

    # Task outcomes from the run history. last_runs is a capped recent
    # window (not a full archive), so counts are "recent runs within the
    # week" — fine for a weekly report, documented here so nobody
    # mistakes it for an exact ledger.
    cutoff_naive = since_local.replace(tzinfo=None)
    task_rows = []
    for task in _task_store().list("all"):
        runs = []
        for run in task.get("last_runs", []):
            try:
                run_at = datetime.fromisoformat(str(run.get("ts", "")))
            except ValueError:
                continue
            if run_at >= cutoff_naive:
                runs.append(run)
        if not runs and task.get("status") != "active":
            continue
        outcomes: dict[str, int] = {}
        for run in runs:
            status = str(run.get("status", "unknown"))
            outcomes[status] = outcomes.get(status, 0) + 1
        task_rows.append({
            "id": task["id"],
            "title": task["title"],
            "status": task["status"],
            "runs_this_week": len(runs),
            "outcomes": outcomes,
            "last_result": (task.get("last_result") or "")[:160],
        })

    daily_budget_usd = float(os.environ.get("HOMUNCULUS_DAILY_BUDGET_USD", "0") or "0")

    # Pre-formatted message with the numbers baked in. The model reported
    # "¢500 of ¢1000" from a result that said 42.0 of 210.0 (live,
    # 2026-07-25) — a weak model transcribing figures into its own prose
    # is a fabrication surface. Giving it a ready-to-send body makes the
    # correct message the laziest possible action.
    weekly_budget_cents = daily_budget_usd * 100 * 7
    suggested = (
        f"🗓 Weekly check-in — {now_local.date().isoformat()}\n\n"
        f"This week:\n"
        f"• {s['notifies']} deliveries · {s['task_failures']} failure(s) recorded\n"
        f"• spend ¢{s['cost_cents']:.0f} of ¢{weekly_budget_cents:.0f} weekly budget\n"
        f"• {s['llm_calls']} model calls · "
        f"{s['input_tokens'] + s['output_tokens']:,} tokens"
    )

    return json.dumps({
        "suggested_message": suggested,
        "note": (
            "Send suggested_message via notify(), appending any nudges the "
            "playbook asks for. NEVER alter the numbers — they are computed "
            "from the event ledger and must reach the user exactly."
        ),
        "window": {
            "from": since_local.isoformat(timespec="seconds"),
            "to": now_local.isoformat(timespec="seconds"),
        },
        "cost": {
            "total_cents": round(s["cost_cents"], 2),
            "weekly_budget_cents": round(daily_budget_usd * 100 * 7, 2),
            "per_day_cents": {d: round(c, 2) for d, c in s["cost_per_day"].items()},
        },
        "llm": {
            "calls": s["llm_calls"],
            "input_tokens": s["input_tokens"],
            "output_tokens": s["output_tokens"],
            "cached_tokens": s["cached_tokens"],
        },
        "activity": {
            "events": s["events"],
            "unique_tools": s["unique_tools"],
            "notifications_sent": s["notifies"],
            "tasks_completed": s["tasks_fired"],
            "task_failures_recorded": s["task_failures"],
            "calls_blocked_by_guards": s["blocked"],
            "memory_writes": s["memory_writes"],
            "memory_forgets": s["memory_forgets"],
        },
        "tasks": task_rows,
    }, indent=2)
