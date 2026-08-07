"""LLM provider layer — HTTP client, provider fallback chain, budget + cooldown.

Extracted from core.py (Phase 2 of the core refactor). This module owns
everything between the agent loop and the model API: the configured providers
and their fallbacks, the synchronous and streaming `call_llm` round-trips,
payload shaping (cache-control, reasoning effort, provider constraints), the
daily-budget gate, and per-provider cooldown after a 429. It has no dependency
on the Agent or the guards, so it imports cleanly with no cycle; core.py
re-exports its public names for backward compatibility.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

import httpx

from homunculus import events
from homunculus.config import get_config

log = logging.getLogger(__name__)


class ProviderExhaustedError(RuntimeError):
    """Every provider in the fallback chain failed for one request.

    Typed so callers can classify infrastructure trouble exactly
    (heartbeat marks the task partial and retries) instead of substring-
    matching a formatted message. The message keeps the historical
    "All providers exhausted: ..." prefix for log continuity and for
    call sites that only see the string.
    """


# --- Config ---------------------------------------------------------------

# API endpoint and model are configurable via env vars so you can swap
# providers/models without code edits. Defaults target Gemini 2.5 Flash —
# the highest-quality free model with 1M context and strong tool use.
# To experiment with others, set HOMUNCULUS_API_URL and/or
# HOMUNCULUS_MODEL in .env.
API_URL = os.environ.get(
    "HOMUNCULUS_API_URL",
    "https://openrouter.ai/api/v1/chat/completions",
)
# Primary model: openai/gpt-oss-120b on OpenRouter.
# Why this over Gemini 2.5 Flash (the previous primary):
#   - Intelligence index 33 vs Flash's 21 (artificialanalysis.ai, Nov 2026)
#   - Blended price $0.20/M vs Flash's $0.33/M — cheaper AND smarter
#   - Strong tool-calling on τ²-Bench, verified working in our fallback
#     chain for weeks
#   - 131K context (vs Flash's 1M) is irrelevant for our workload:
#     measured max ever sent = 31K, p99 = 21K, p50 = 6K. Plenty of headroom.
# The structural failures we kept patching (re-delivers same task, skips
# complete_task, ignores ordered skill steps) are long-context instruction-
# following failures Flash exhibits at our prompt sizes. gpt-oss-120b's
# higher intelligence index correlates directly with reliability on
# those classes of task.
MODEL = os.environ.get("HOMUNCULUS_MODEL", "openai/gpt-oss-120b")

# Fallback provider chain. Each slot is independent — set only the keys
# you have. On 429 from one provider, we move to the next; a 429'd
# provider is benched for config.provider.cooldown_seconds so we don't
# hammer a throttled endpoint.
#
# Slot 1 (HOMUNCULUS_API_KEY_FALLBACK): OpenRouter — Kimi K2.6 and Qwen3 are
#   purpose-built for agentic tool-calling; both have free tiers with generous
#   weekly limits. Comma-separated: we try each model before moving to the next
#   provider. kimi-k2.6 first (262K ctx, top agentic benchmark), then qwen3.
# Slot 2 (HOMUNCULUS_API_KEY_FALLBACK_2): Groq — llama-3.3-70b-versatile is
#   fast and reliable; good speed fallback when OpenRouter free tiers are busy.
# Slot 3 (HOMUNCULUS_API_KEY_FALLBACK_3): Cerebras — fast inference,
#   generous TPM. Get key at cloud.cerebras.ai.
API_URL_FALLBACK = os.environ.get(
    "HOMUNCULUS_API_URL_FALLBACK",
    "https://openrouter.ai/api/v1/chat/completions",
)
MODEL_FALLBACK = os.environ.get(
    "HOMUNCULUS_MODEL_FALLBACK",
    # When the primary (paid gpt-oss-120b) is throttled or unreachable,
    # fall through these in order. All free-tier OpenRouter routes; all
    # support tool calling.
    #   openai/gpt-oss-120b:free  — free tier of the same model as primary
    #                               (different rate-limit pool, useful when
    #                               paid hits a transient 429)
    #   meta-llama/llama-3.3-70b-instruct:free — 131K ctx, Meta-hosted
    # Deliberately excluded (don't re-add):
    #   moonshotai/kimi-k2.6:free — slug is paid-only, returns 404
    #     ("unavailable for free").
    #   qwen/qwen3-coder:free — OpenRouter routes it to a deprecated upstream
    #     model (Venice qwen3-coder-480b-a35b-instruct); returns 404.
    "openai/gpt-oss-120b:free,meta-llama/llama-3.3-70b-instruct:free",
)

API_URL_FALLBACK_2 = os.environ.get(
    "HOMUNCULUS_API_URL_FALLBACK_2",
    "https://api.groq.com/openai/v1/chat/completions",
)
MODEL_FALLBACK_2 = os.environ.get(
    "HOMUNCULUS_MODEL_FALLBACK_2", "llama-3.3-70b-versatile"
)

API_URL_FALLBACK_3 = os.environ.get(
    "HOMUNCULUS_API_URL_FALLBACK_3",
    "https://api.cerebras.ai/v1/chat/completions",
)
MODEL_FALLBACK_3 = os.environ.get("HOMUNCULUS_MODEL_FALLBACK_3", "gpt-oss-120b")

# Some providers (Groq, Cerebras) sit behind Cloudflare which returns 403
# when the User-Agent is absent or looks like a raw Python script.
_HTTP_HEADERS_BASE = {"User-Agent": "homunculus/1.0 (httpx)"}

# Provider cooldowns + retry policy now live in HomunculusConfig.provider
# (see config.py). Callsites below use get_config().provider.* directly so
# tests can override via set_config(...) without monkey-patching this module.
#
# Docs cheat-sheet:
#   cooldown_seconds              — how long a 429'd provider stays cooled
#   unavailable_cooldown_seconds  — same for 5xx / connect failures (longer)
#   primary_max_retry_wait        — max Retry-After we'll honour from primary
#   primary_default_retry_wait    — retry wait when 429 omits Retry-After
#   enforce_daily_budget          — block calls that cross the daily envelope

_MODEL_PRICING_CENTS: dict[str, tuple[float, float]] = {
    # cents per 1M input, cents per 1M output (updated June 2026)
    "gemini-2.5-flash":                         (15.0,  60.0),
    "gemini-2.5-pro":                           (125.0, 1000.0),
    "gemini-2.0-flash":                         (10.0,  40.0),
    "llama-3.3-70b-versatile":                  (59.0,  79.0),
    "llama-3.1-8b-instant":                     (5.0,   8.0),
    "openai/gpt-4o":                            (250.0, 1000.0),
    "openai/gpt-4o-mini":                       (15.0,  60.0),
    "openai/gpt-4.1-mini":                      (40.0,  160.0),
    "anthropic/claude-sonnet-5":                (200.0, 1000.0),
    "anthropic/claude-sonnet-4.6":              (300.0, 1500.0),
    "anthropic/claude-haiku-4.5":               (100.0, 500.0),
    "deepseek/deepseek-v3":                     (14.0,  28.0),
    "deepseek/deepseek-v4-flash-0731":          (14.0,  28.0),
    "deepseek/deepseek-v4-pro":                 (43.5,  87.0),
    "qwen/qwen3.7-flash":                       (3.0,   13.0),
    # OpenRouter list price as of 2026-08; provider competition has cut it
    # several times, so refresh when the budget report looks inflated.
    "openai/gpt-oss-120b":                      (3.7,   17.0),
}

# Module-level cooldown cache: url|model -> wall-clock expiry timestamp.
# A provider/model pair is "cooled" if time.time() < its expiry.
_PROVIDER_COOLDOWN: dict[str, float] = {}


# --- HTTP layer -----------------------------------------------------------

def _providers(model_override: str | None) -> list[tuple[str, str, str]]:
    """Return ordered (url, api_key, model) chain, skipping cooled providers.

    Slots (each an env-configured url/key/model triple; defaults:
    OpenRouter gpt-oss-120b primary, then Gemini, OpenRouter free
    models, Cerebras):
      0: primary — required (HOMUNCULUS_API_KEY / _API_URL / _MODEL)
      1: fallback — if HOMUNCULUS_API_KEY_FALLBACK
      2: fallback 2 — if HOMUNCULUS_API_KEY_FALLBACK_2
      3: fallback 3 — if HOMUNCULUS_API_KEY_FALLBACK_3

    Empty key = slot skipped. Cooled provider (recent 429) = slot
    skipped until cooldown expires. If ALL providers are cooled we
    still return at least one so the caller can attempt rather than
    crash on empty list — the call will likely 429 again and we'll
    re-cool, which is fine.
    """
    raw_slots = [
        (API_URL, os.environ.get("HOMUNCULUS_API_KEY", ""), model_override or MODEL),
        (API_URL_FALLBACK, os.environ.get("HOMUNCULUS_API_KEY_FALLBACK", ""), MODEL_FALLBACK),
        (API_URL_FALLBACK_2, os.environ.get("HOMUNCULUS_API_KEY_FALLBACK_2", ""), MODEL_FALLBACK_2),
        (API_URL_FALLBACK_3, os.environ.get("HOMUNCULUS_API_KEY_FALLBACK_3", ""), MODEL_FALLBACK_3),
    ]
    raw = [
        (url, key, model_id)
        for url, key, model_spec in raw_slots
        for model_id in _expand_model_spec(model_spec)
    ]
    have_keys = [p for p in raw if p[1]]
    now = time.time()
    fresh = [p for p in have_keys if _PROVIDER_COOLDOWN.get(_provider_key(p[0], p[2]), 0) <= now]
    return fresh or have_keys[:1]  # never return empty if any keys are set


def _expand_model_spec(model_spec: str) -> list[str]:
    """Split comma-separated model ids, preserving a single-model default."""
    models = [m.strip() for m in model_spec.split(",") if m.strip()]
    return models or [model_spec]


def _provider_key(url: str, model_id: str) -> str:
    return f"{url}|{model_id}"


def _budget_cents() -> float:
    raw = os.environ.get("HOMUNCULUS_DAILY_BUDGET_USD", "0") or "0"
    try:
        return max(0.0, float(raw) * 100)
    except ValueError:
        return 0.0


# Pricing assumed for a paid model that is missing from the table. Set at
# frontier-model rates on purpose: the budget must fail CLOSED on cost, so an
# unlisted paid model (e.g. swapped in via the chat `/use` command) is counted
# and blockable at worst-case prices rather than invisibly free. Add real
# pricing to _MODEL_PRICING_CENTS to cost a new model accurately.
_DEFAULT_PAID_PRICING_CENTS: tuple[float, float] = (250.0, 1000.0)


def _pricing_for(model_id: str) -> tuple[float, float] | None:
    """(input, output) cents-per-1M for a model, or None when it can't
    cost money (free-tier route or empty id)."""
    if not model_id or model_id.endswith(":free"):
        return None
    return _MODEL_PRICING_CENTS.get(model_id, _DEFAULT_PAID_PRICING_CENTS)


#: Emit the "budget accounting degraded" alarm at most once per process so a
#: persistently-corrupt log doesn't spam the event stream every tick.
_budget_degraded_warned = False


def _warn_budget_degraded(reason: str) -> None:
    """Surface a corrupted/unreadable events log — the budget ceiling silently
    relies on it. Without this, accounting fails open (spend reads as 0 = "full
    budget") and the one hard cost guarantee evaporates with no breadcrumb."""
    global _budget_degraded_warned
    if _budget_degraded_warned:
        return
    _budget_degraded_warned = True
    log.warning("budget accounting degraded: %s — spend will under-count", reason)
    try:
        events.emit("budget_accounting_degraded", reason=reason)
    except Exception:
        pass


def _budget_cutoff_utc() -> datetime:
    """Start of the budget window: the user's local midnight, in UTC.

    Local midnight, not UTC — otherwise the budget appears to roll over
    at 05:30 IST for an IST user.
    """
    try:
        from homunculus.user_tz import get_user_tz_name
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(get_user_tz_name())
        local_midnight = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
        return local_midnight.astimezone(UTC)
    except Exception:
        return datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def _record_cost_cents(rec: dict) -> float:
    """Cost of one llm_call event record, in cents. 0 for free routes."""
    model = rec.get("model") or rec.get("name") or ""
    pricing = _pricing_for(model)
    if pricing is None:
        return 0.0
    input_tok = int(rec.get("input_tokens") or 0)
    output_tok = int(rec.get("output_tokens") or 0)
    cached_tok = int(rec.get("cached_tokens") or 0)
    price_in, price_out = pricing
    uncached = max(0, input_tok - cached_tok)
    return (uncached * price_in + cached_tok * price_in * 0.1 + output_tok * price_out) / 1_000_000


#: Incremental spend accounting. The events log only ever grows between
#: rotations, so after one full scan we remember (path, window, byte offset,
#: subtotal) and each later check parses only the appended bytes — instead of
#: re-reading a multi-MB file on every paid-model call. Invalidated whenever
#: the path changes (tests), the file shrinks (rotation), or the budget
#: window rolls over at local midnight.
_spend_cache: dict[str, Any] | None = None


def _today_spend_cents() -> float:
    """Estimate today's spend from llm_call events.

    This mirrors the dashboard's accounting. It is intentionally conservative
    and best-effort: if the log is missing or malformed, return 0 so the agent
    does not brick itself because observability failed. But a genuinely
    *corrupt* log (present, non-empty, yet unparseable) is reported via
    _warn_budget_degraded — silently failing open on the budget ceiling is the
    one accounting failure worth shouting about.
    """
    global _spend_cache
    events_path = Path(os.environ.get("HOMUNCULUS_EVENTS_PATH", "_events.jsonl"))
    if not events_path.exists():
        return 0.0
    cutoff = _budget_cutoff_utc()

    try:
        size = events_path.stat().st_size
    except OSError as e:
        _warn_budget_degraded(f"events log unreadable ({e.__class__.__name__})")
        return 0.0

    cache = _spend_cache
    if (
        cache is not None
        and cache["path"] == str(events_path)
        and cache["cutoff"] == cutoff
        and size >= cache["offset"]
    ):
        if size == cache["offset"]:
            return cache["subtotal"]
        added, new_offset = _scan_spend_bytes(events_path, cache["offset"], cutoff)
        if new_offset is not None:
            cache["subtotal"] += added
            cache["offset"] = new_offset
            return cache["subtotal"]
        # Read failed mid-flight — fall through to a full rescan.

    total, offset = _scan_spend_bytes(events_path, 0, cutoff, warn_on_corruption=True)
    if offset is None:
        return 0.0
    _spend_cache = {
        "path": str(events_path),
        "cutoff": cutoff,
        "offset": offset,
        "subtotal": total,
    }
    return total


def _scan_spend_bytes(
    events_path: Path,
    start_offset: int,
    cutoff: datetime,
    warn_on_corruption: bool = False,
) -> tuple[float, int | None]:
    """Sum llm_call spend in [start_offset, last complete line], in cents.

    Returns (subtotal, next_offset). next_offset stops at the byte after the
    last newline so a line another process is mid-append never gets half-
    counted and then skipped — it's re-read complete on the next check.
    (subtotal, None) on read failure.
    """
    try:
        with events_path.open("rb") as f:
            f.seek(start_offset)
            blob = f.read()
    except OSError as e:
        if warn_on_corruption:
            _warn_budget_degraded(f"events log unreadable ({e.__class__.__name__})")
        return 0.0, None
    last_nl = blob.rfind(b"\n")
    if last_nl == -1:
        return 0.0, start_offset
    complete = blob[: last_nl + 1]
    total = 0.0
    parsed_any = False
    saw_line = False
    for line in complete.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        saw_line = True
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        parsed_any = True
        if rec.get("event") != "llm_call":
            continue
        ts_raw = rec.get("ts", "")
        try:
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
        except ValueError:
            continue
        if ts < cutoff:
            continue
        total += _record_cost_cents(rec)
    # A non-empty log where not one line parsed as JSON is corruption, not a
    # quiet day — and it would silently zero out the budget ceiling.
    if warn_on_corruption and saw_line and not parsed_any:
        _warn_budget_degraded("events log present but no records parsed")
    return total, start_offset + last_nl + 1


def measure_llm_usage_since(
    cutoff_ts: datetime,
) -> dict[str, float | int]:
    """Aggregate LLM token counts and cost from events.jsonl since cutoff.

    Used by the task layer to attribute per-task spend. We scan
    backward from the end of the events log and stop once we cross
    the cutoff timestamp, so the scan is bounded by recent activity
    not the full log size. Returns zeros if the file is missing or
    no llm_call events lie in the window.

    Output shape:
        {input_tokens, output_tokens, cached_tokens, cost_cents, calls}
    """
    out: dict[str, float | int] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_tokens": 0,
        "cost_cents": 0.0,
        "calls": 0,
    }
    events_path = Path(os.environ.get("HOMUNCULUS_EVENTS_PATH", "_events.jsonl"))
    if not events_path.exists():
        return out
    try:
        lines = events_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    cutoff = cutoff_ts if cutoff_ts.tzinfo is not None else cutoff_ts.replace(tzinfo=UTC)
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts_raw = rec.get("ts", "")
        try:
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
        except ValueError:
            continue
        if ts < cutoff:
            break
        if rec.get("event") != "llm_call":
            continue
        in_tok = int(rec.get("input_tokens") or 0)
        out_tok = int(rec.get("output_tokens") or 0)
        cached_tok = int(rec.get("cached_tokens") or 0)
        out["input_tokens"] += in_tok
        out["output_tokens"] += out_tok
        out["cached_tokens"] += cached_tok
        out["calls"] += 1
        out["cost_cents"] += _record_cost_cents(rec)
    return out


#: Warn once per process when a paid model runs with enforcement on but no
#: budget configured — otherwise "the ceiling is off" is invisible until the
#: bill arrives.
_budget_disabled_warned = False


def _budget_blocks_model(model_id: str) -> bool:
    global _budget_disabled_warned
    if not get_config().provider.enforce_daily_budget:
        return False
    if _pricing_for(model_id) is None:
        return False  # free route — nothing to cap
    budget = _budget_cents()
    if budget <= 0:
        if not _budget_disabled_warned:
            _budget_disabled_warned = True
            log.warning(
                "budget enforcement is on but HOMUNCULUS_DAILY_BUDGET_USD is "
                "unset/0 — paid model %s runs with NO cost ceiling",
                model_id,
            )
        return False
    return _today_spend_cents() >= budget


# Wall-clock timestamp of the most recent provider cool event. Used by
# the agent's system-prompt builder to inject a rate-limit-awareness
# heads-up when something just got throttled — the agent can checkpoint
# via continue_task instead of burning through fallbacks blindly.
_PROVIDER_LAST_COOLED_AT: float = 0.0


def _cool_provider(
    url: str,
    model_id: str,
    seconds: float | None = None,
) -> None:
    """Mark a provider as temporarily unavailable; skip until expiry."""
    if seconds is None:
        seconds = get_config().provider.cooldown_seconds
    global _PROVIDER_LAST_COOLED_AT
    _PROVIDER_COOLDOWN[_provider_key(url, model_id)] = time.time() + seconds
    _PROVIDER_LAST_COOLED_AT = time.time()


def _recent_provider_cool_seconds() -> float | None:
    """How long since the most recent provider cool, or None if never."""
    if _PROVIDER_LAST_COOLED_AT == 0.0:
        return None
    return time.time() - _PROVIDER_LAST_COOLED_AT


def _is_transient_provider_error(response: httpx.Response) -> bool:
    """Return True for provider/model-routing errors worth falling back from.

    Covers three classes:
    1. Rate-limit / quota  (429, 413 context-too-large) — try next provider.
    2. Routing / infra     (404 "no endpoints", 502/503/504) — try next.
    3. Model capability    (400 output_parse_failed, tool_use_failed) — the
       model on this provider can't handle the request; next provider may do
       better. Distinct from a genuinely bad request (wrong API key etc.)
       which we still raise immediately.
    """
    if response.status_code == 429:
        return True
    # 413: context exceeds this provider's token limit — try a larger one.
    if response.status_code == 413:
        return True
    try:
        response.read()
    except Exception:
        pass
    if response.status_code in {404, 502, 503, 504}:
        # ANY 404 from a chat-completions endpoint is a this-provider
        # problem — model slug removed, free tier paywalled, route
        # deprecated. Raising can't fix it; the next provider in the
        # chain is independent. This must cover the full 404 family: a
        # body like "This model is unavailable for free..." (a slug that
        # went paid) is just as fatal as "no endpoints", and a narrow
        # substring check would let the raise kill a delivery while
        # healthy fallback providers sit unused.
        return True
    if response.status_code == 400:
        # Model capability failures — not a bug in our request, but the
        # model on this slot can't output valid tool JSON. Try next.
        body = response.text.lower()
        return (
            "output_parse_failed" in body
            or "tool_use_failed" in body
            or "tool call validation failed" in body
        )
    return False


# Keys we attach to history messages for our own bookkeeping but never
# want to leak into the LLM request. Providers vary on strictness — most
# OpenAI-compatible endpoints tolerate extras, but Anthropic-via-OpenRouter
# and some others reject unknown keys outright.
_INTERNAL_MESSAGE_KEYS = frozenset({"source", "ts"})


def _strip_internal_fields(messages: list[dict]) -> list[dict]:
    return [
        {k: v for k, v in m.items() if k not in _INTERNAL_MESSAGE_KEYS}
        for m in messages
    ]


# Providers whose backend supports OpenAI-style ephemeral cache_control
# markers on message content. OpenRouter passes through to Anthropic /
# certain OpenAI models, both of which honour the breakpoint. Gemini's
# native API uses a totally different cachedContent reference model;
# we don't try to support that yet. We match on the host substring so
# custom proxies (e.g. self-hosted OpenRouter mirror) still benefit if
# the user names them with the canonical hostname.
_CACHE_CONTROL_PROVIDER_HOSTS = ("openrouter.ai", "anthropic.com")


def _provider_supports_cache_control(url: str) -> bool:
    u = url.lower()
    return any(host in u for host in _CACHE_CONTROL_PROVIDER_HOSTS)


def _is_openrouter(url: str) -> bool:
    return "openrouter.ai" in url.lower()


def _apply_max_tokens(payload: dict, url: str, model_id: str) -> None:
    """Set a reasonable max_tokens for billing-aware providers.

    OpenRouter (Anthropic-family models specifically) *reserves credit
    up to max_tokens* on every request — without an explicit value it
    defaults to the model's max (~64K for Haiku/Sonnet), reserves the
    full cost of that output, and 402s ("This request requires more
    credits, or fewer max_tokens") whenever your balance is below the
    reservation. Real call output is almost always < 2K tokens so the
    reservation is wildly over-sized.

    Setting an explicit cap keeps the reservation accurate without
    constraining real responses (4096 is plenty — we cap actual
    outputs via the loop budget, not the per-call ceiling).

    Only OpenRouter exhibits this behavior; direct provider routes
    (Gemini, Groq, Cerebras) don't reserve credit and silently accept
    short outputs. We apply the cap only for OpenRouter URLs so other
    providers stay at their own defaults.
    """
    if not _is_openrouter(url):
        return
    # 4096 is enough for the longest legitimate single-turn output we've
    # seen (the LeetCode notify message with code block was ~588 tokens;
    # a verbose reasoning model peaks around 2K). Headroom for outliers.
    payload.setdefault("max_tokens", 4096)


def _apply_provider_constraints(
    payload: dict, url: str, constraints: dict | None,
) -> None:
    """OpenRouter-specific routing constraints.

    OpenRouter routes the same model id across multiple inference
    providers (Novita, DeepInfra, DekaLLM, Google, ...). Some honor
    `tool_choice: "required"` reliably; others let the model bail to
    text. The `provider.require_parameters: true` constraint tells
    OpenRouter "only route to providers that actually support all the
    request params I sent" — which transitively pins us to ones that
    enforce tool_choice when we set it.

    Only takes effect for OpenRouter URLs. Other providers (Gemini,
    Groq, Cerebras) terminate at a single provider, so routing
    constraints are meaningless.
    """
    if not constraints or not _is_openrouter(url):
        return
    payload["provider"] = dict(constraints)


def _maybe_add_cache_control(messages: list[dict], url: str) -> list[dict]:
    """Add an ephemeral cache_control breakpoint to the system message
    when the provider supports it.

    The system message + tool schemas + AGENTS.md are stable across
    most calls within a session — together they're ~7-8K tokens of
    pure overhead. With cache hits, Anthropic charges ~10% of normal
    for cached tokens; cumulative cost over a tick can drop by 60-70%.

    No-op for providers that don't recognise the cache_control field —
    sending it to Gemini would just be silently ignored, but the
    structured-content shape might trip stricter validators, so we
    only convert when we know it's safe.

    Returns a NEW list (does not mutate caller's input).
    """
    if not _provider_supports_cache_control(url):
        return messages
    if not messages or messages[0].get("role") != "system":
        return messages
    head = messages[0]
    raw = head.get("content")
    if not isinstance(raw, str):
        # Already structured — leave alone rather than risk
        # double-wrapping. The agent's own pipeline always uses strings.
        return messages
    cached_head = {
        **head,
        "content": [
            {
                "type": "text",
                "text": raw,
                "cache_control": {"type": "ephemeral"},
            }
        ],
    }
    return [cached_head, *messages[1:]]


def _build_payload(
    messages: list[dict],
    tool_schemas: list[dict] | None,
    url: str,
    model_id: str,
    tool_choice: str | dict,
    reasoning_effort: str,
    provider_constraints: dict | None,
    stream: bool = False,
) -> dict[str, Any]:
    """Assemble one chat-completions request body for a specific provider.

    Everything provider-shape-aware happens here (cache-control conversion,
    reasoning effort, routing constraints, the OpenRouter max_tokens cap),
    so the blocking and streaming callers cannot drift apart.
    """
    payload: dict[str, Any] = {
        "model": model_id,
        "messages": _maybe_add_cache_control(
            _strip_internal_fields(messages), url,
        ),
    }
    if stream:
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}
    if tool_schemas is not None:
        payload["tools"] = tool_schemas
        payload["tool_choice"] = tool_choice
        payload["parallel_tool_calls"] = False
    _apply_reasoning_effort(payload, model_id, url, reasoning_effort)
    _apply_provider_constraints(payload, url, provider_constraints)
    _apply_max_tokens(payload, url, model_id)
    return payload


def _emit_budget_blocked(model_id: str, url: str, reason: str) -> None:
    try:
        events.emit(
            "budget_blocked",
            name=model_id,
            model=model_id,
            host=_url_host(url),
            result=reason,
        )
    except Exception:
        pass


def _attempt_chain(
    messages: list[dict],
    tool_schemas: list[dict] | None,
    model: str | None,
    tool_choice: str | dict,
    reasoning_effort: str,
    provider_constraints: dict | None,
    allow_primary_retry: bool,
) -> tuple[dict | None, str]:
    """One pass over the provider chain. Returns (assistant_msg, last_err);
    assistant_msg is None when every provider failed transiently.

    Failure classification per provider:
      - connection error / 429 / transient status / malformed 200 → cool the
        provider and move to the next slot;
      - a hard 4xx (bad key, malformed request) → raise immediately, no
        fallback can fix our own request;
      - budget-blocked paid model → skip with a budget_blocked event.

    `allow_primary_retry` gates the wait-and-retry-once on a primary 429
    (honoured on the first pass; the second pass already slept between
    passes, so retrying inline again would just double the stall).
    """
    last_err = ""
    for idx, (url, key, model_id) in enumerate(_providers(model)):
        if _budget_blocks_model(model_id):
            last_err = f"daily budget exhausted; skipping paid model {model_id}"
            _emit_budget_blocked(model_id, url, last_err)
            continue
        payload = _build_payload(
            messages, tool_schemas, url, model_id,
            tool_choice, reasoning_effort, provider_constraints,
        )
        headers = {**_HTTP_HEADERS_BASE, "Authorization": f"Bearer {key}"}

        try:
            response = httpx.post(url, headers=headers, json=payload, timeout=60.0)
        except httpx.HTTPError as e:
            last_err = f"{type(e).__name__}: {e}"
            _cool_provider(url, model_id)
            continue

        if response.status_code == 429:
            last_err = response.text
            retry_after = _parse_retry_after(response)
            # Primary-provider retry: if this is the first provider we tried
            # and the retry-after is short (or absent — Gemini often omits it),
            # wait and retry once before falling to lower-quality fallbacks.
            wait_for = (
                retry_after if retry_after is not None
                else get_config().provider.primary_default_retry_wait
            )
            if (
                allow_primary_retry
                and idx == 0
                and wait_for <= get_config().provider.primary_max_retry_wait
            ):
                log.warning(f"[call_llm] {model_id} 429, waiting {wait_for:.0f}s — retrying primary")
                time.sleep(wait_for)
                try:
                    retry_resp = httpx.post(url, headers=headers, json=payload, timeout=60.0)
                    if retry_resp.status_code == 200:
                        rj = retry_resp.json()
                        msg = _extract_assistant_message(rj)
                        if msg is not None:
                            _emit_llm_call(model_id, url, messages, rj.get("usage"))
                            return msg, last_err
                        last_err = f"malformed 200 response: {retry_resp.text[:200]}"
                    # Retry also failed — fall through to cool + continue.
                    retry_after = _parse_retry_after(retry_resp)
                except httpx.HTTPError:
                    pass
            cool_for = retry_after if retry_after else get_config().provider.cooldown_seconds
            _cool_provider(url, model_id, cool_for)
            log.warning(f"[call_llm] {model_id} 429 → cooling {cool_for:.0f}s, trying next")
            try:
                events.emit("provider_cooled", name=model_id, host=_url_host(url), result=f"429 · cooling {cool_for:.0f}s")
            except Exception:
                pass
            continue
        if _is_transient_provider_error(response):
            last_err = response.text
            _cool_provider(url, model_id, get_config().provider.unavailable_cooldown_seconds)
            log.info(
                f"[call_llm] {model_id} unavailable ({response.status_code}) "
                "→ cooling 10m, trying next",
            )
            continue
        if response.status_code >= 400:
            raise RuntimeError(f"API error {response.status_code}: {response.text}")
        try:
            rj = response.json()
        except (ValueError, json.JSONDecodeError):
            last_err = f"non-JSON 200 response: {response.text[:200]}"
            _cool_provider(url, model_id, get_config().provider.unavailable_cooldown_seconds)
            log.warning(f"[call_llm] {model_id} returned non-JSON 200 → cooling, trying next")
            continue
        msg = _extract_assistant_message(rj)
        if msg is None:
            last_err = f"malformed 200 response (missing choices/message): {response.text[:200]}"
            _cool_provider(url, model_id, get_config().provider.unavailable_cooldown_seconds)
            log.warning(f"[call_llm] {model_id} 200 with no choices → cooling, trying next")
            try:
                events.emit("provider_cooled", name=model_id, host=_url_host(url), result="200/no-choices")
            except Exception:
                pass
            continue
        # Emit which model actually answered, so the live feed shows it.
        _emit_llm_call(model_id, url, messages, rj.get("usage"))
        return msg, last_err
    return None, last_err


def call_llm(
    messages: list[dict],
    tool_schemas: list[dict] | None,
    model: str | None = None,
    tool_choice: str | dict = "auto",
    reasoning_effort: str = "low",
    provider_constraints: dict | None = None,
) -> dict:
    """One round-trip to the LLM chat completions endpoint.

    Returns the assistant message dict, shape:
      {"role": "assistant",
       "content": str | None,
       "tool_calls": [...] | None}

    Walks the provider chain (see _attempt_chain for per-provider failure
    handling); if the whole chain fails transiently, sleeps briefly and
    walks it once more — usually one provider's cooldown has expired by
    then. Raises ProviderExhaustedError when both passes come up empty.

    tool_schemas=None makes it a plain-chat call (no tool use).
    model defaults to MODEL; services can override per-call.

    tool_choice controls whether the model MUST call a tool ("required")
    or merely MAY ("auto", default). Forcing every turn to end in a tool
    call removes the prose-vs-tool ambiguity that eats heartbeat
    deliveries when the model drafts content as assistant_reply instead
    of as notify(text=...). This mirrors the "required tool call" idea
    seen in agents like Letta and Pi, which expose the same per-call knob.

    May also be passed as the OpenAI-shaped dict
    `{"type": "function", "function": {"name": "<tool>"}}` to force one
    specific tool. This is the Pi state-machine primitive — each state
    in a per-task pipeline pins exactly one tool, so the model can't
    skip steps. Passed through verbatim; OpenAI/OpenRouter accept both
    forms natively.
    """
    primary_key = os.environ.get("HOMUNCULUS_API_KEY")
    if not primary_key:
        raise RuntimeError("HOMUNCULUS_API_KEY is not set.")

    msg, last_err = _attempt_chain(
        messages, tool_schemas, model, tool_choice,
        reasoning_effort, provider_constraints, allow_primary_retry=True,
    )
    if msg is not None:
        return msg

    # All providers in this attempt 429'd or errored. Honor a short sleep
    # then retry the chain once — usually one provider's cooldown will
    # have expired by then.
    wait = 30.0
    log.warning(f"[call_llm] all providers cooled; sleeping {wait:.0f}s and retrying once")
    time.sleep(wait)
    msg, retry_err = _attempt_chain(
        messages, tool_schemas, model, tool_choice,
        reasoning_effort, provider_constraints, allow_primary_retry=False,
    )
    if msg is not None:
        return msg

    raise ProviderExhaustedError(f"All providers exhausted: {retry_err or last_err}")


def _extract_assistant_message(rj: dict) -> dict | None:
    """Pull the assistant message out of an OpenAI-shape response.

    Returns None if the response is malformed (missing `choices`,
    empty list, missing `message`). Callers should treat None as a
    transient provider error and try the next provider — observed
    live 2026-06-10: a provider returned 200 with no `choices` key
    and crashed the heartbeat tick mid-state-machine with a KeyError.
    """
    if not isinstance(rj, dict):
        return None
    choices = rj.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    msg = first.get("message")
    if not isinstance(msg, dict):
        return None
    return msg


def _url_host(url: str) -> str:
    """Extract the host part of a URL for compact event labels."""
    try:
        return url.split("://", 1)[1].split("/", 1)[0]
    except (IndexError, AttributeError):
        return url


def _apply_reasoning_effort(
    payload: dict, model_id: str, url: str = "", effort: str = "low",
) -> None:
    """Set OpenRouter `reasoning.effort` for models that default to high CoT.

    gpt-oss-* on OpenRouter returns chain-of-thought in a separate
    `reasoning` field by default — at HIGH effort, which consumes all
    the output token budget before the model produces actual content
    or a tool_call. The empirical symptom is `finish_reason: "length"`
    with `content: null`.

    The `reasoning` request param is an OpenRouter-specific extension
    (also OpenAI's o1 API). Direct providers (Cerebras, Groq, Gemini)
    reject the request with HTTP 400 when they see an unknown param —
    so we gate by URL host. When fallback routing hits one of those
    providers we silently skip the param.

    For execution-mode loops (heartbeat, chat) the iteration IS the
    reasoning structure — each tick is a "thought" — so effort=low
    frees the output budget for the actual tool call.

    For design-mode loops (skill refinement) the model needs multi-
    step internal reasoning to explore options, debug API contracts,
    and verify approaches. The caller passes effort="medium" or "high".
    """
    if "gpt-oss" not in model_id:
        return
    # Cerebras/Gemini/Groq direct routes reject unknown params with 400.
    # Only set the field when we know the provider parses it.
    if url and not _is_openrouter(url):
        return
    payload["reasoning"] = {"effort": effort}


def _emit_llm_call(model_id: str, url: str, messages: list[dict], usage: dict | None) -> None:
    """Emit an llm_call event with token counts extracted from the usage dict."""
    kwargs: dict[str, Any] = {
        "name": model_id,
        "model": model_id,
        "host": _url_host(url),
        "request": _serialize_messages(messages),
    }
    if usage:
        kwargs["input_tokens"] = usage.get("prompt_tokens", 0)
        kwargs["output_tokens"] = usage.get("completion_tokens", 0)
        # OpenAI-compatible cached token field (varies by provider)
        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
        kwargs["cached_tokens"] = cached
    events.emit("llm_call", **kwargs)


def _serialize_messages(messages: list[dict]) -> str:
    """Serialize the last 6 messages to JSON for trace display.

    Truncates very long content fields to keep the feed readable.
    """
    _MAX_CONTENT = 2000
    trimmed = []
    for m in messages[-6:]:
        entry: dict = {"role": m.get("role", "?")}
        content = m.get("content")
        if isinstance(content, str) and len(content) > _MAX_CONTENT:
            entry["content"] = content[:_MAX_CONTENT] + f"…[+{len(content)-_MAX_CONTENT}]"
        elif content is not None:
            entry["content"] = content
        if m.get("tool_calls"):
            entry["tool_calls"] = m["tool_calls"]
        if m.get("tool_call_id"):
            entry["tool_call_id"] = m["tool_call_id"]
        trimmed.append(entry)
    return json.dumps(trimmed, indent=2)


def call_llm_stream(
    messages: list[dict],
    tool_schemas: list[dict] | None,
    model: str | None = None,
    tool_choice: str | dict = "auto",
    reasoning_effort: str = "low",
    provider_constraints: dict | None = None,
):
    """Streaming variant of call_llm.

    Generator yielding tuples of (kind, payload) as chunks arrive:
        ("content", "<delta text>")   — append to assistant content
        ("tool_call_delta", <dict>)   — partial tool_call to accumulate
        ("done", <assistant_msg>)     — final reconstructed message

    Real LLM streaming and tool-use don't compose cleanly: until we see
    chunks we don't know whether this turn is a text reply or a tool
    call. We forward content deltas immediately (so the user sees text
    appear word-by-word) and silently accumulate tool_call deltas. At
    [DONE] we yield the assembled message — caller can check for
    tool_calls then.

    No retry on 429 (the SSE response complicates retry); caller can
    fall back to non-streaming call_llm for retry semantics if needed.
    """
    primary_key = os.environ.get("HOMUNCULUS_API_KEY")
    if not primary_key:
        raise RuntimeError("HOMUNCULUS_API_KEY is not set.")

    # Walk the provider chain, cooling any that 429. Open the stream
    # on whichever provider answers first without a rate-limit.
    last_err = ""
    response_ctx = None
    response = None
    used_url = ""
    used_model = ""
    for idx, (url, key, model_id) in enumerate(_providers(model)):
        if _budget_blocks_model(model_id):
            last_err = f"daily budget exhausted; skipping paid model {model_id}"
            _emit_budget_blocked(model_id, url, last_err)
            continue
        payload = _build_payload(
            messages, tool_schemas, url, model_id,
            tool_choice, reasoning_effort, provider_constraints, stream=True,
        )
        try:
            response_ctx = httpx.stream(
                "POST",
                url,
                headers={**_HTTP_HEADERS_BASE, "Authorization": f"Bearer {key}"},
                json=payload,
                timeout=120.0,
            )
            response = response_ctx.__enter__()
        except httpx.HTTPError as e:
            last_err = f"{type(e).__name__}: {e}"
            _cool_provider(url, model_id)
            response_ctx = None
            response = None
            continue
        if response.status_code == 429:
            response.read()
            last_err = response.text
            retry_after = _parse_retry_after(response)
            # Primary-provider retry: wait and retry the primary once before
            # falling to lower-quality fallbacks. When no Retry-After header is
            # present (Gemini often omits it), wait the short default instead.
            wait_for = retry_after if (retry_after is not None) else get_config().provider.primary_default_retry_wait
            if idx == 0 and wait_for <= get_config().provider.primary_max_retry_wait:
                response_ctx.__exit__(None, None, None)
                response_ctx = None
                response = None
                log.warning(f"[call_llm_stream] {model_id} 429, waiting {wait_for:.0f}s — retrying primary")
                time.sleep(wait_for)
                try:
                    response_ctx = httpx.stream(
                        "POST",
                        url,
                        headers={**_HTTP_HEADERS_BASE, "Authorization": f"Bearer {key}"},
                        json=payload,
                        timeout=120.0,
                    )
                    response = response_ctx.__enter__()
                    if response.status_code == 200:
                        # Retry succeeded — break out to streaming logic below
                        used_url = url
                        used_model = model_id
                        break
                    # Retry also failed — clean up and fall through to cool+continue
                    retry_after = _parse_retry_after(response)
                    response_ctx.__exit__(None, None, None)
                    response_ctx = None
                    response = None
                except httpx.HTTPError:
                    response_ctx = None
                    response = None
            if response_ctx is None:
                cool_for = retry_after if retry_after else get_config().provider.cooldown_seconds
                _cool_provider(url, model_id, cool_for)
                log.warning(f"[call_llm_stream] {model_id} 429 → cooling {cool_for:.0f}s, trying next")
                continue
            # No retry was attempted (not primary, or retry_after too long) —
            # close the original 429 stream and cool this provider.
            cool_for = retry_after if retry_after else get_config().provider.cooldown_seconds
            response_ctx.__exit__(None, None, None)
            response_ctx = None
            response = None
            _cool_provider(url, model_id, cool_for)
            log.warning(f"[call_llm_stream] {model_id} 429 → cooling {cool_for:.0f}s, trying next")
            continue
        if _is_transient_provider_error(response):
            response.read()
            last_err = response.text
            _status = response.status_code
            response_ctx.__exit__(None, None, None)
            response_ctx = None
            response = None
            _cool_provider(url, model_id, get_config().provider.unavailable_cooldown_seconds)
            log.info(
                f"[call_llm_stream] {model_id} unavailable ({_status}) "
                "→ cooling 10m, trying next",
            )
            continue
        if response.status_code >= 400:
            response.read()
            err = response.text
            response_ctx.__exit__(None, None, None)
            raise RuntimeError(f"API error {response.status_code}: {err}")
        used_url = url
        used_model = model_id
        break

    if response_ctx is None or response is None:
        raise ProviderExhaustedError(f"All providers exhausted: {last_err}")

    content_acc: list[str] = []
    tool_calls_acc: dict[int, dict[str, Any]] = {}
    stream_usage: dict | None = None

    try:
        for raw_line in response.iter_lines():
            if not raw_line:
                continue
            line = raw_line.strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            if chunk.get("usage"):
                stream_usage = chunk["usage"]
            choices = chunk.get("choices") or []
            if not choices:
                continue
            # Detect token-limit truncation — provider stopped mid-generation.
            # Append a visible marker so the user knows the reply was cut off.
            if choices[0].get("finish_reason") == "length":
                content_acc.append("\n\n⚠️ *(response truncated — model hit token limit)*")
            delta = choices[0].get("delta") or {}

            if "content" in delta and delta["content"]:
                content_acc.append(delta["content"])
                yield ("content", delta["content"])

            for tc_delta in delta.get("tool_calls") or []:
                idx = tc_delta.get("index", 0)
                slot = tool_calls_acc.setdefault(idx, {
                    "id": "",
                    "type": "function",
                    "function": {"name": "", "arguments": ""},
                })
                if tc_delta.get("id"):
                    slot["id"] = tc_delta["id"]
                fn = tc_delta.get("function") or {}
                if fn.get("name") and not slot["function"]["name"]:
                    # Set name only once — some providers (Gemini) re-send the
                    # full tool name in every streaming chunk instead of just
                    # the first. Concatenating would give "web_searchweb_search".
                    slot["function"]["name"] = fn["name"]
                if fn.get("arguments"):
                    slot["function"]["arguments"] += fn["arguments"]
                yield ("tool_call_delta", slot)
    finally:
        response_ctx.__exit__(None, None, None)

    _emit_llm_call(used_model, used_url, messages, stream_usage)

    # Assemble the final assistant message in the shape the loop expects.
    # Validate tool-call arguments parse as JSON — sometimes a stream
    # ends mid-arguments (network drop, provider error) and we'd hand
    # the agent a half-baked tool call that crashes on json.loads later.
    # Repair by clearing the bad arguments string to "{}" so the tool
    # runs with no args (the LLM will see the empty result and retry).
    if tool_calls_acc:
        for slot in tool_calls_acc.values():
            args = slot["function"].get("arguments") or ""
            if not args.strip():
                slot["function"]["arguments"] = "{}"
                continue
            try:
                json.loads(args)
            except json.JSONDecodeError:
                # Truncated/broken JSON. Best we can do is hand the
                # model an empty-args call so it surfaces the issue
                # rather than crashing the whole turn.
                log.info(
                    f"[stream] tool {slot['function'].get('name','?')} args "
                    f"didn't parse, defaulting to {{}}: {args[:120]!r}",
                )
                slot["function"]["arguments"] = "{}"

    assistant_msg: dict[str, Any] = {"role": "assistant"}
    if content_acc:
        assistant_msg["content"] = "".join(content_acc)
    if tool_calls_acc:
        assistant_msg["tool_calls"] = [tool_calls_acc[i] for i in sorted(tool_calls_acc)]
    yield ("done", assistant_msg)


def _parse_retry_after(response: httpx.Response) -> float | None:
    """Extract retry-after delay (seconds) from response, or None.

    Groq sends it as a numeric header value. Some providers send a
    timestamp instead; we keep it simple here and only handle the
    numeric-seconds case.
    """
    header = response.headers.get("retry-after")
    if header is None:
        return None
    try:
        return float(header)
    except ValueError:
        return None
