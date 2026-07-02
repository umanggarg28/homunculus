"""Deterministic aggregation over the shared event log (_events.jsonl).

Single source of truth for "what did the agent do and what did it
cost". Consumed by the web API (/api/stats/today) and by the agent's
own week_in_review tool — both surfaces must report identical numbers,
so the counting lives here and nowhere else. The LLM never computes
these figures; it only formats them.

Window limit: heartbeat rotates the event log to the last 14 days
(events.rotate), so any `since` older than that silently undercounts.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, tzinfo, UTC
from pathlib import Path

def model_cost_cents(model: str, input_tok: int, output_tok: int, cached_tok: int) -> float:
    """Estimated cost in cents for one LLM call. Free models → 0.

    Delegates to the budget enforcer's pricing (llm._pricing_for) so every
    surface — sidebar budget, Overview spend, per-run trace cost, the
    week_in_review tool — reports the SAME number the daily ceiling counts.
    This module once carried its own pricing table; the two copies drifted
    and the UI showed ¢0.0 for the primary model while the enforcer was
    accruing real spend. One pricing source, like one lock primitive.
    """
    from homunculus.llm import _pricing_for  # deferred: llm loads .env at import

    pricing = _pricing_for(model)
    if pricing is None:
        return 0.0
    price_in_cents, price_out_cents = pricing  # cents per 1M tokens
    uncached = max(0, input_tok - cached_tok)
    return (
        uncached * price_in_cents
        + cached_tok * price_in_cents * 0.1
        + output_tok * price_out_cents
    ) / 1_000_000


def events_path() -> Path:
    return Path(os.environ.get("HOMUNCULUS_EVENTS_PATH", "_events.jsonl"))


def summarize_events(
    since: datetime,
    *,
    path: Path | None = None,
    tz: tzinfo | None = None,
) -> dict:
    """Count activity in the event log at/after `since` (tz-aware).

    `tz` controls the calendar used for the per-day cost buckets
    (default UTC). Reads the file newest-line-first and stops at the
    first record older than `since` — the log is append-ordered, so
    this skips the bulk of old lines on every call.
    """
    if since.tzinfo is None:
        raise ValueError("since must be timezone-aware")
    bucket_tz = tz or UTC

    total_events = 0
    unique_tools: set[str] = set()
    tasks_fired = 0
    task_failures = 0
    notifies = 0
    blocked = 0
    memory_writes = 0
    memory_forgets = 0
    llm_calls = 0
    input_tokens = 0
    output_tokens = 0
    cached_tokens = 0
    cost_cents = 0.0
    cost_per_day: dict[str, float] = {}

    p = path or events_path()
    lines: list[str] = []
    if p.exists():
        try:
            with p.open("r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            lines = []

    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            ts = datetime.fromisoformat(str(rec.get("ts", "")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        if ts < since:
            break

        total_events += 1
        evt = rec.get("event", "")
        if evt == "tool_call":
            name = rec.get("name") or ""
            if name:
                unique_tools.add(name)
            if name == "complete_task":
                tasks_fired += 1
            elif name == "record_failure":
                task_failures += 1
            elif name == "notify":
                notifies += 1
        elif evt == "tool_blocked":
            blocked += 1
        elif evt == "memory_write":
            memory_writes += 1
        elif evt == "memory_forget":
            memory_forgets += 1
        elif evt == "llm_call":
            in_tok = int(rec.get("input_tokens") or 0)
            out_tok = int(rec.get("output_tokens") or 0)
            ca_tok = int(rec.get("cached_tokens") or 0)
            llm_calls += 1
            input_tokens += in_tok
            output_tokens += out_tok
            cached_tokens += ca_tok
            cost = model_cost_cents(rec.get("model", ""), in_tok, out_tok, ca_tok)
            cost_cents += cost
            day = ts.astimezone(bucket_tz).date().isoformat()
            cost_per_day[day] = cost_per_day.get(day, 0.0) + cost

    return {
        "since": since.isoformat(),
        "events": total_events,
        "unique_tools": sorted(unique_tools),
        "tasks_fired": tasks_fired,
        "task_failures": task_failures,
        "notifies": notifies,
        "blocked": blocked,
        "memory_writes": memory_writes,
        "memory_forgets": memory_forgets,
        "llm_calls": llm_calls,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_tokens": cached_tokens,
        "cost_cents": cost_cents,
        "cost_per_day": {d: round(c, 4) for d, c in sorted(cost_per_day.items())},
    }
