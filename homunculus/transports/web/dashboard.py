"""Overview-dashboard routes — today's activity totals and the context gauge.

Both are read-only derivations over the event stream that drive the landing
Overview panels; neither mutates state.
"""

import json
import os
from datetime import datetime, UTC
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from homunculus.stats import summarize_events
from homunculus.transports import web_api as wa

router = APIRouter()


@router.get("/api/stats/today", dependencies=[Depends(wa.require_web_auth)])
def stats_today() -> JSONResponse:
    """Return activity counts since the user's local midnight today.

    Windows on the *user's* timezone (not UTC) so the budget visibly
    resets when the user's calendar day flips, not at 05:30 IST.
    Falls back to UTC if user_tz isn't set yet.
    """
    try:
        from homunculus.user_tz import get_user_tz_name
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(get_user_tz_name())
        local_midnight = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
        cutoff = local_midnight.astimezone(UTC)
    except Exception:
        cutoff = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)

    s = summarize_events(cutoff)
    budget_usd = float(os.environ.get("HOMUNCULUS_DAILY_BUDGET_USD", "0") or "0")
    return JSONResponse({
        "since": s["since"],
        "events": s["events"],
        "unique_tools": len(s["unique_tools"]),
        "tasks_fired": s["tasks_fired"],
        "memory_writes": s["memory_writes"],
        "memory_forgets": s["memory_forgets"],
        "input_tokens": s["input_tokens"],
        "output_tokens": s["output_tokens"],
        "cached_tokens": s["cached_tokens"],
        "cost_cents": round(s["cost_cents"], 2),
        "budget_cents": round(budget_usd * 100, 2),
    })


# Known context limits for common model IDs (tokens).
_CONTEXT_LIMITS: dict[str, int] = {
    # Gemini
    "gemini-2.5-flash":              1_048_576,
    "gemini-2.5-pro":                1_048_576,
    "gemini-2.0-flash":              1_048_576,
    "gemini-1.5-flash":              1_048_576,
    "gemini-1.5-pro":                2_097_152,
    # OpenAI
    "gpt-4o":                          128_000,
    "gpt-4o-mini":                     128_000,
    "gpt-4-turbo":                     128_000,
    "gpt-4":                            32_768,
    "gpt-oss-120b":                    128_000,
    # Groq / Meta Llama
    "llama-3.3-70b-versatile":         128_000,
    "llama-3.3-70b-instruct":          131_072,
    "llama-3.2-3b-instruct":           131_072,
    # OpenRouter free fallbacks (verified June 2026)
    "kimi-k2":                         262_144,   # moonshotai/kimi-k2.6:free
    "qwen3-coder":                   1_048_576,   # qwen/qwen3-coder:free
    "qwen3-next":                      262_144,   # qwen/qwen3-next-80b-a3b-instruct:free
    "hermes-3":                        131_072,   # nousresearch/hermes-3-llama-3.1-405b:free
    "gemma-4":                         262_144,   # google/gemma-4-31b-it:free
    # DeepSeek / Anthropic
    "deepseek":                        163_840,
    "claude-haiku":                    200_000,
    "claude-sonnet":                   200_000,
}


def _context_limit_for(model: str) -> int:
    """Return the context window size for a model ID, falling back to 128k."""
    for key, limit in _CONTEXT_LIMITS.items():
        if key in model:
            return limit
    return 128_000


@router.get("/api/context", dependencies=[Depends(wa.require_web_auth)])
def context_gauge() -> JSONResponse:
    """Return the latest prompt token count and model context limit.

    Scans _events.jsonl from the end for the most recent llm_call event
    that has input_tokens. The prompt_tokens value from the API response
    IS the full current context size (cumulative, not incremental).
    """
    events_path = Path(os.environ.get("HOMUNCULUS_EVENTS_PATH", "_events.jsonl"))
    last_input_tokens: int = 0
    last_model: str = os.environ.get("HOMUNCULUS_MODEL", "gemini-2.5-flash")

    if events_path.exists():
        try:
            with events_path.open("r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("event") == "llm_call" and rec.get("input_tokens"):
                    last_input_tokens = rec["input_tokens"]
                    if rec.get("model"):
                        last_model = rec["model"]
                    break
        except OSError:
            pass

    limit = _context_limit_for(last_model)
    return JSONResponse({
        "used_tokens": last_input_tokens,
        "limit_tokens": limit,
        "model": last_model,
        "pct": round(last_input_tokens / limit * 100, 1) if limit else 0,
    })
