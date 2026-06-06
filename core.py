"""
Homunculus core: the agent loop.

This is the heart of the project — the entire "agent" concept lives in
the Agent.chat() method below. About 120 lines.

What it does:
  1. Sends a message history + tool schemas to an LLM via raw HTTP.
  2. If the LLM responds with tool calls, runs each one and feeds the
     results back into the next request.
  3. Loops until the LLM responds with a normal text answer (no tool
     calls), then returns that answer.

No SDK, no framework. Just httpx and JSON.
"""

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from dotenv import load_dotenv

import events
import tools
from memory import Memory
from tasks import TaskStore

# Output guard — compiled once at module load.
_GUARD_MEMORY_FILENAME_RE = re.compile(
    r"\b(?:user|feedback|project|reference|skill)_[a-z0-9_]+\.md\b"
)
_GUARD_INTERNAL_PATHS = ("workspace/memory/", "memory/logs/", "memory/_")
_GUARD_ERROR_PREFIXES = ("ERROR:", "ERROR running ")
_GUARD_CONFABULATION_TERMS = ("example.com", "example domain")

# Phrases that indicate the model is claiming to have performed an action.
# When combined with zero tool calls, this is a hallucination.
_GUARD_ACTION_CLAIM_PHRASES = (
    "i've created", "i have created",
    "i've set", "i have set",
    "i've added", "i have added",
    "i've scheduled", "i have scheduled",
    "i've sent", "i have sent",
    "i've saved", "i have saved",
    "i've updated", "i have updated",
    "i've deleted", "i have deleted",
    "i've removed", "i have removed",
    "task has been created", "task was created",
    "reminder has been set", "reminder was set",
    "notification has been", "notification was sent",
    "done! i've", "done. i've",
)

# Map tool names → which arg holds the targeted resource (file path or
# URL). The claim/result consistency check uses this to recover the
# "target" of a tool call, so it can match a path mentioned in the reply
# against the tool calls that touched it.
_CLAIM_TARGET_TOOLS: dict[str, str] = {
    "read_file": "path",
    "write_file": "path",
    "append_file": "path",
    "web_fetch": "url",
}

# Phrases preceding a positive-action claim about a specific target.
# Tuned for false-negative bias: if a reply uses any of these followed
# by a path/URL, we look up whether the matching tool call actually
# succeeded.
_CLAIM_VERBS_RE = re.compile(
    r"(?i)\b(?:i\s+(?:successfully\s+)?(?:read|found|fetched|wrote|"
    r"saved|opened|loaded|retrieved|got)|(?:successfully|just)\s+"
    r"(?:read|fetched|wrote|saved|loaded|retrieved))\b"
)

# Match a path or URL in the reply. Allows /etc/foo, /tmp/bar.yaml, and
# https://example.com/x. Conservative — only catches absolute paths and
# fully-qualified URLs, since relative names like "config" are too noisy.
_CLAIM_TARGET_RE = re.compile(
    r"(?:`|'|\"|^|\s)"
    r"(/[A-Za-z0-9_.\-/]+|https?://[^\s'\"`]+)"
    r"(?:`|'|\"|\.|\s|$)"
)


def _claim_target_inconsistencies(reply: str, tool_outcomes: list[dict]) -> list[str]:
    """Find paths/URLs the reply claims to have acted on successfully,
    where every matching tool call this turn failed.

    Returns the list of (target) strings that triggered. Empty list = no
    inconsistency. The check is intentionally conservative: it only
    fires when (a) a claim verb appears in the reply, (b) followed by an
    absolute path or fully-qualified URL within a few words, and (c)
    EVERY tool call against that target in this turn returned an error.
    """
    # Quick exit: no claim verbs → nothing to check.
    if not _CLAIM_VERBS_RE.search(reply):
        return []

    # Build a map: target → list of success-bools across this turn's calls.
    target_outcomes: dict[str, list[bool]] = {}
    for outcome in tool_outcomes:
        arg_name = _CLAIM_TARGET_TOOLS.get(outcome.get("name", ""))
        if not arg_name:
            continue
        target = (outcome.get("args") or {}).get(arg_name)
        if not isinstance(target, str) or not target.strip():
            continue
        target_outcomes.setdefault(target, []).append(bool(outcome.get("success")))

    if not target_outcomes:
        return []

    # For each claim verb occurrence, scan forward up to ~120 chars for
    # a target and check whether any tool call against it succeeded.
    inconsistent: list[str] = []
    for verb_match in _CLAIM_VERBS_RE.finditer(reply):
        window = reply[verb_match.end(): verb_match.end() + 120]
        for tgt_match in _CLAIM_TARGET_RE.finditer(window):
            target = tgt_match.group(1)
            outcomes = target_outcomes.get(target)
            if outcomes is None:
                continue
            if not any(outcomes):
                inconsistent.append(target)
    return inconsistent


# Load .env at module import so config reads below see its values. Safe
# to call twice (main.py also calls it) — load_dotenv won't overwrite
# env vars that are already set, e.g. by docker-compose's env_file.
load_dotenv(Path(__file__).parent / ".env")


# --- Config ---------------------------------------------------------------

# API endpoint and model are configurable via env vars so you can swap
# providers/models without code edits. Defaults target Gemini 2.5 Flash —
# the highest-quality free model with 1M context and strong tool use.
# To experiment with others, set HOMUNCULUS_API_URL and/or
# HOMUNCULUS_MODEL in .env.
API_URL = os.environ.get(
    "HOMUNCULUS_API_URL",
    "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
)
MODEL = os.environ.get("HOMUNCULUS_MODEL", "gemini-2.5-flash")

# Fallback provider chain. Each slot is independent — set only the keys
# you have. On 429 from one provider, we move to the next; a 429'd
# provider is benched for PROVIDER_COOLDOWN_SECONDS so we don't hammer
# a throttled endpoint.
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
    # Verified against OpenRouter /api/v1/models June 2026 — all :free, all support tool calling.
    # kimi-k2.6: 262K ctx, purpose-built for agentic tool use.
    # qwen3-coder: 1M ctx, strong tool calling.
    # llama-3.3-70b-instruct: Meta, 131K ctx, verified tools support.
    # gpt-oss-120b: OpenAI MoE 117B, 131K ctx, verified tools+tool_choice support.
    "moonshotai/kimi-k2.6:free,qwen/qwen3-coder:free,meta-llama/llama-3.3-70b-instruct:free,openai/gpt-oss-120b:free",
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

# How long to bench a provider after it returns 429. During this window
# we skip it entirely and route to the next provider in the chain. 60s
# is long enough for most rate-limit windows to refill (Groq TPM is
# per-minute, Gemini RPM is per-minute).
PROVIDER_COOLDOWN_SECONDS = 60.0
PROVIDER_UNAVAILABLE_COOLDOWN_SECONDS = 10 * 60.0
# If the primary provider 429s with a retry-after at or below this threshold,
# wait and retry it once before falling to lower-quality fallbacks.
# Keeps quality high for transient rate limits — a short wait is better than
# degrading to a weaker model.
PRIMARY_MAX_RETRY_WAIT = 45.0
# When the primary 429s with no Retry-After header, wait this many seconds and
# retry once before falling to weaker fallbacks. Gemini often omits the header.
PRIMARY_DEFAULT_RETRY_WAIT = 8.0

# Optional hard budget guard. The dashboard already reports estimated spend;
# this flag turns that estimate into enforcement. Free endpoints (`:free`)
# and unknown-priced models are allowed so the agent can degrade to free
# providers instead of going completely dark.
ENFORCE_DAILY_BUDGET = os.environ.get(
    "HOMUNCULUS_ENFORCE_DAILY_BUDGET",
    "",
).strip().lower() in {"1", "true", "yes", "on"}

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
    "anthropic/claude-sonnet-4-6":              (300.0, 1500.0),
    "anthropic/claude-haiku-4-5":               (100.0, 500.0),
    "deepseek/deepseek-v3":                     (14.0,  28.0),
}

# Module-level cooldown cache: url|model -> wall-clock expiry timestamp.
# A provider/model pair is "cooled" if time.time() < its expiry.
_PROVIDER_COOLDOWN: dict[str, float] = {}

# Hard cap on tool-use iterations per user turn. Without this, a broken
# LLM could call tools forever. 20 is plenty for any realistic task.
MAX_TURNS = 20

# Read-only tools whose results can be safely cached within a single turn.
# When the LLM re-calls one of these with the same arguments, the harness
# returns the cached result + a hint instead of re-executing. Saves an
# LLM call's worth of cycles AND prevents the worse stuck-loop error
# path (which fires at count 3 and tells the model to "pivot" — useful
# only for true non-idempotent loops). Tools with side effects (notify,
# complete_task, write_file, python with mutations) are deliberately
# excluded — their results should NEVER be cached.
READ_ONLY_CACHEABLE_TOOLS = frozenset({
    "read_file",
    "recall",
    "search_files",
    "get_current_time",
    "list_tasks",
    "get_world_state",
    "conversation_search",
    "archival_memory_search",
    "web_fetch",      # idempotent on a given URL within a turn
    "web_search",     # query is the cache key; same query → same answer
})

# Hard cap on per-tool-result content that lands in conversation history.
# Without this, a single web_fetch of a long page (e.g. a 50KB GitHub
# README markdown) dumps the entire payload into history, leaving no
# token budget for the final LLM call that would actually USE the data.
# The agent loop then drops into the empty-content fallback path and
# the task never completes.
#
# 6000 chars ≈ 1500 tokens — enough to capture the gist of most useful
# tool results while preserving room for reasoning. The harness adds a
# hint at the cut so the agent knows it can refine the query to see more.
#
# Note: this only affects what's appended to history. The `tool_result`
# event emitted to _events.jsonl keeps the full payload (truncated for
# display by the UI's per-kind COMPACT_LEN_BY_KIND) so the Traces page
# can still show the complete tool output for debugging.
TOOL_RESULT_HARD_CAP = 6000

# Per-tool caps. Default falls back to TOOL_RESULT_HARD_CAP. Tighter
# caps reflect what the agent actually needs from each tool's result:
# - web_fetch: usually only the lead paragraphs matter; the rest is
#   nav + footer. 2500 chars (~625 tok) is enough for the gist.
# - python: stdout-heavy executions are useful but usually the tail
#   matters more than the head (final value, exception trace).
# - write_file: a "Wrote N bytes" confirmation is all that's needed.
# - notify: same — a "delivered" confirmation.
# - get_current_time: trivially short.
# - update_world_state: confirmation only.
_PER_TOOL_RESULT_CAPS: dict[str, int] = {
    "web_fetch": 2500,
    "python": 4000,
    "write_file": 400,
    "notify": 400,
    "get_current_time": 200,
    "update_world_state": 200,
    "complete_task": 300,
    "continue_task": 400,
    "cancel_task": 300,
    "load_tool": 300,
    "mark_fired": 200,
    "schedule_next_tick": 200,
    "schedule_task": 300,
}

# Tools whose final ~20% of output matters more than the head — keep
# both ends when we need to truncate. python stdout usually has the
# final result / exception trace at the tail.
_TAIL_PRESERVING_TOOLS = frozenset({"python"})


def _trim_tool_result_for_history(name: str, result: object) -> object:
    """Cap oversized tool result strings before they enter conversation history.

    Non-string results pass through unchanged (some tools return dicts/lists).
    ERROR strings under the cap pass through (they're already short).
    Anything over the cap gets head-truncated (or head+tail for tools
    where the tail is informative) with a clear hint that the agent
    can re-call with a more targeted query.

    Per-tool caps live in `_PER_TOOL_RESULT_CAPS`; missing entries fall
    back to `TOOL_RESULT_HARD_CAP`. Tightening these per-tool was a
    surprising win — web_fetch alone was dumping 6K of page chrome
    into history when the agent only needed the lead paragraphs.
    """
    if not isinstance(result, str):
        return result
    cap = _PER_TOOL_RESULT_CAPS.get(name, TOOL_RESULT_HARD_CAP)
    if len(result) <= cap:
        return result
    hint_budget = 200
    body_budget = cap - hint_budget

    if name in _TAIL_PRESERVING_TOOLS:
        # head + tail split, weighted 70/30. python stdout often has
        # the final value or exception trace at the very end.
        head_chars = int(body_budget * 0.7)
        tail_chars = body_budget - head_chars
        elided = len(result) - body_budget
        body = (
            f"{result[:head_chars]}\n\n"
            f"[... {elided:,} chars elided ...]\n\n"
            f"{result[-tail_chars:]}"
        )
        return (
            f"{body}\n\n"
            f"[harness: trimmed {elided:,} chars from the middle. "
            f"Tail preserved for tools like python where the final value "
            f"or exception is most informative.]"
        )

    head_chars = body_budget
    trimmed_count = len(result) - head_chars
    return (
        f"{result[:head_chars]}\n\n"
        f"[+{trimmed_count:,} chars trimmed by harness — re-call with a "
        f"more targeted query/path if needed, or use recall() / "
        f"search_files() to find specific content]"
    )

# Stub written in place of an old tool result. Includes original char
# count so the agent knows the result existed and can re-call the tool
# if it still needs the payload. tool_call_id is preserved on the
# message itself, so the API's tool_call/tool_result pairing rule holds.
_EVICTED_TOOL_RESULT_TEMPLATE = (
    "[tool result evicted — was {chars:,} chars. "
    "Re-call the tool if you still need this content; the full payload "
    "is in the events log.]"
)


def _evict_prior_tool_results(history: list[dict], keep_recent: int = 0) -> int:
    """Replace tool-message content with a short stub, leaving the
    most recent `keep_recent` tool messages untouched.

    Two call sites:

    1. **Between user turns** (`keep_recent=0`): every tool result from
       prior turns becomes a stub. The assistant has already consumed
       them to produce its reply; keeping full payloads burns tokens
       linearly with conversation length.

    2. **Between iterations within an agent loop** (`keep_recent=2`):
       only the most recent 2 tool results stay full-fidelity. The
       agent's next reasoning step needs to look at the last one or
       two tool calls it made; anything older was already reasoned
       over and condensed into the assistant's next decision. Without
       this, a tool-heavy task (web_fetch + web_search + read_file +
       …) sees per-call input grow linearly across iterations — at
       iter 15 the agent is re-sending 90K+ of stale tool payloads on
       every LLM call. That's why a "simple" task can burn 500K+
       tokens in one run on free-tier providers.

    `tool_call_id` is preserved on the message itself, so the API's
    tool_call/tool_result pairing rule still holds.

    Returns the number of messages that were stubbed (for logging).
    Idempotent — already-stubbed messages are skipped.
    """
    # Indices of full (non-stubbed) tool messages, in order. We
    # protect the last `keep_recent` of these from eviction.
    full_tool_idxs = [
        i for i, m in enumerate(history)
        if m.get("role") == "tool"
        and isinstance(m.get("content"), str)
        and not m["content"].startswith("[tool result evicted")
    ]
    protected = set(full_tool_idxs[-keep_recent:]) if keep_recent else set()

    evicted = 0
    for i in full_tool_idxs:
        if i in protected:
            continue
        msg = history[i]
        msg["content"] = _EVICTED_TOOL_RESULT_TEMPLATE.format(chars=len(msg["content"]))
        evicted += 1
    return evicted


# Mid-session compaction thresholds.
#
# COMPACT_TRIGGER counts USER turns (not raw messages). When we exceed
# this many user turns we summarize older ones into a single system-role
# summary. COMPACT_KEEP_RECENT is the number of recent user turns kept
# verbatim. We count user turns because tool-heavy assistant responses
# can balloon raw message count by 10× without adding any new user
# context — compacting on raw count was dropping context way too
# aggressively (2-3 conversational turns triggered it).
COMPACT_TRIGGER = 8   # heartbeat sessions are short; compact sooner to stay under context limits
COMPACT_KEEP_RECENT = 4


def _resolve_agents_md():
    """Locate the AGENTS.md identity file, or None if absent.

    Search order:
      1. $HOMUNCULUS_AGENTS_MD if set
      2. /app/AGENTS.md (Docker image path — production)
      3. ./AGENTS.md relative to cwd (local dev)

    Returns a Path or None. Cached at first call — restart to pick up
    new locations, but file CONTENTS are re-read on every Agent
    construction (since each heartbeat tick instantiates a fresh Agent).
    """
    from pathlib import Path as _P
    import os as _os
    explicit = _os.environ.get("HOMUNCULUS_AGENTS_MD")
    if explicit:
        p = _P(explicit)
        return p if p.exists() else None
    for candidate in (_P("/app/AGENTS.md"), _P("AGENTS.md")):
        if candidate.exists():
            return candidate
    return None


SYSTEM_PROMPT = """You are Homunculus — an autonomous personal assistant with persistent memory.

Paths: your cwd IS the workspace. Use plain relative paths like `notes.md` or
`memory/logs/2026/05/2026-05-19.md`. Never prefix with `workspace/`.

Memory:
- Your MEMORY INDEX appears below — one line per entry. Pinned facts (user
  profile, key rules) are included directly above the index.
- To read the full body of any memory, call recall(query) with relevant keywords.
  Nothing is injected automatically — you decide when context is needed.
- Before remember(), check if an entry already covers the fact. Reuse the same
  `name` to overwrite rather than create a duplicate.
- If a memory is contradicted or obsolete, forget() it.
- Types: user · feedback · project · reference · skill (learned procedures)

Scheduling:
- For recurring commitments use create_task(recurrence="daily"|"weekly").
  create_task() will automatically update an existing task if the title
  matches — you never need to check for duplicates manually.
- schedule_next_tick is for one-shot wake timers only; never for recurring.

World state:
- Call get_world_state() at the start of any multi-step task to check if
  prior steps already completed (safe after restarts/interruptions).
- Call update_world_state() as you progress: set focus, active_task, step,
  last_ok. This lets you resume safely and lets the UI show live status.
- Call rate_skill(name, outcome) after using a skill_*.md procedure so the
  system can learn what works and flag skills that need refinement.

Self-extension (Pi-style):
- For non-trivial Python you'll want again later — data parsing, a
  fetch+transform, a report builder — write the code to
  `scripts/<slug>.py` with write_file, then run it via:
      code = read_file("scripts/<slug>.py")
      python(code)
  The sandbox is ephemeral, but scripts/ persists across sessions.
  Treat scripts/ as your toolbox. Refine in place; reuse next time.

Behaviour:
- Cite full URLs (https://…) when you use web_search results — never
  "result 1".
- If web_fetch is blocked (401/403/429), do NOT retry the same URL.
  Use snippets, your knowledge, or a different source.
- notify() interrupts the user's phone. Reserve for time-sensitive
  things; routine output goes in files.
- Short, direct replies. When a task is complete, summarise in one or
  two lines — don't call more tools.
- If a follow-up is ambiguous and you have no grounded context from
  this conversation to answer it, ask for clarification. One sentence.
- Do not mention memory-internal filenames (e.g. feedback_*.md,
  project_*.md) unprompted — use plain language like "my notes" or
  "delivery log". Exception: when the user explicitly asks you to search
  or list files, ALWAYS call search_files or list_files and report the
  exact file paths and line numbers from the tool result — never
  paraphrase or answer from context instead of running the tool.
  Search from path="." (workspace root) unless the user names a specific
  subfolder.
"""


# --- HTTP layer -----------------------------------------------------------

def _providers(model_override: str | None) -> list[tuple[str, str, str]]:
    """Return ordered (url, api_key, model) chain, skipping cooled providers.

    Slots:
      0: primary (Groq) — required
      1: fallback (Gemini by default) — if HOMUNCULUS_API_KEY_FALLBACK
      2: fallback 2 (OpenRouter by default) — if HOMUNCULUS_API_KEY_FALLBACK_2
      3: fallback 3 (Cerebras by default) — if HOMUNCULUS_API_KEY_FALLBACK_3

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


def _is_known_paid_model(model_id: str) -> bool:
    if not model_id or model_id.endswith(":free"):
        return False
    return model_id in _MODEL_PRICING_CENTS


def _today_spend_cents() -> float:
    """Estimate today's spend from llm_call events.

    This mirrors the dashboard's accounting. It is intentionally conservative
    and best-effort: if the log is missing or malformed, return 0 so the agent
    does not brick itself because observability failed.
    """
    events_path = Path(os.environ.get("HOMUNCULUS_EVENTS_PATH", "_events.jsonl"))
    if not events_path.exists():
        return 0.0
    # Window on the user's local midnight, not UTC — otherwise the
    # budget appears to roll over at 05:30 IST for an IST user.
    try:
        from user_tz import get_user_tz_name
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(get_user_tz_name())
        local_midnight = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
        cutoff = local_midnight.astimezone(timezone.utc)
    except Exception:
        cutoff = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    total = 0.0
    try:
        lines = events_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return 0.0
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts_raw = rec.get("ts", "")
        try:
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if ts < cutoff:
            break
        if rec.get("event") != "llm_call":
            continue
        model = rec.get("model") or rec.get("name") or ""
        if not _is_known_paid_model(model):
            continue
        input_tok = int(rec.get("input_tokens") or 0)
        output_tok = int(rec.get("output_tokens") or 0)
        cached_tok = int(rec.get("cached_tokens") or 0)
        price_in, price_out = _MODEL_PRICING_CENTS[model]
        uncached = max(0, input_tok - cached_tok)
        total += (uncached * price_in + cached_tok * price_in * 0.1 + output_tok * price_out) / 1_000_000
    return total


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
    cutoff = cutoff_ts if cutoff_ts.tzinfo is not None else cutoff_ts.replace(tzinfo=timezone.utc)
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts_raw = rec.get("ts", "")
        try:
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
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
        model = rec.get("model") or rec.get("name") or ""
        if _is_known_paid_model(model):
            price_in, price_out = _MODEL_PRICING_CENTS[model]
            uncached = max(0, in_tok - cached_tok)
            out["cost_cents"] += (
                uncached * price_in + cached_tok * price_in * 0.1 + out_tok * price_out
            ) / 1_000_000
    return out


def _budget_blocks_model(model_id: str) -> bool:
    if not ENFORCE_DAILY_BUDGET:
        return False
    budget = _budget_cents()
    if budget <= 0 or not _is_known_paid_model(model_id):
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
    seconds: float = PROVIDER_COOLDOWN_SECONDS,
) -> None:
    """Mark a provider as temporarily unavailable; skip until expiry."""
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
        body = response.text.lower()
        return (
            "no endpoints found" in body
            or "no endpoint" in body
            or response.status_code in {502, 503, 504}
        )
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


def call_llm(
    messages: list[dict],
    tool_schemas: list[dict] | None,
    model: str | None = None,
) -> dict:
    """One round-trip to the LLM chat completions endpoint.

    Returns the assistant message dict, shape:
      {"role": "assistant",
       "content": str | None,
       "tool_calls": [...] | None}

    On 429 (rate limited) from the primary provider, automatically tries
    the fallback provider (Gemini by default) if its API key is set in
    env. If the fallback is unset, sleeps for retry-after and retries
    primary once. This gives us elastic capacity without paying for it.

    tool_schemas=None makes it a plain-chat call (no tool use).
    model defaults to MODEL; services can override per-call.
    """
    primary_key = os.environ.get("HOMUNCULUS_API_KEY")
    if not primary_key:
        raise RuntimeError("HOMUNCULUS_API_KEY is not set.")

    providers = _providers(model)
    last_err = ""

    for idx, (url, key, model_id) in enumerate(providers):
        if _budget_blocks_model(model_id):
            last_err = f"daily budget exhausted; skipping paid model {model_id}"
            try:
                events.emit(
                    "budget_blocked",
                    name=model_id,
                    model=model_id,
                    host=_url_host(url),
                    result=last_err,
                )
            except Exception:
                pass
            continue
        payload: dict[str, Any] = {
            "model": model_id,
            "messages": _maybe_add_cache_control(
                _strip_internal_fields(messages), url,
            ),
        }
        if tool_schemas is not None:
            payload["tools"] = tool_schemas
            payload["tool_choice"] = "auto"
            payload["parallel_tool_calls"] = False

        try:
            response = httpx.post(
                url,
                headers={**_HTTP_HEADERS_BASE, "Authorization": f"Bearer {key}"},
                json=payload,
                timeout=60.0,
            )
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
            wait_for = retry_after if (retry_after is not None) else PRIMARY_DEFAULT_RETRY_WAIT
            if idx == 0 and wait_for <= PRIMARY_MAX_RETRY_WAIT:
                print(f"[call_llm] {model_id} 429, waiting {wait_for:.0f}s — retrying primary", flush=True)
                time.sleep(wait_for)
                try:
                    retry_resp = httpx.post(
                        url,
                        headers={**_HTTP_HEADERS_BASE, "Authorization": f"Bearer {key}"},
                        json=payload,
                        timeout=60.0,
                    )
                    if retry_resp.status_code == 200:
                        rj = retry_resp.json()
                        _emit_llm_call(model_id, url, messages, rj.get("usage"))
                        return rj["choices"][0]["message"]
                    # Retry also failed — fall through to cool + continue
                    response = retry_resp
                    retry_after = _parse_retry_after(retry_resp)
                except httpx.HTTPError:
                    pass
            cool_for = retry_after if retry_after else PROVIDER_COOLDOWN_SECONDS
            _cool_provider(url, model_id, cool_for)
            print(f"[call_llm] {model_id} 429 → cooling {cool_for:.0f}s, trying next", flush=True)
            try:
                events.emit("provider_cooled", name=model_id, host=_url_host(url), result=f"429 · cooling {cool_for:.0f}s")
            except Exception:
                pass
            continue
        if _is_transient_provider_error(response):
            last_err = response.text
            _cool_provider(url, model_id, PROVIDER_UNAVAILABLE_COOLDOWN_SECONDS)
            print(
                f"[call_llm] {model_id} unavailable ({response.status_code}) "
                "→ cooling 10m, trying next",
                flush=True,
            )
            continue
        if response.status_code >= 400:
            raise RuntimeError(f"API error {response.status_code}: {response.text}")
        # Emit which model actually answered, so the live feed shows it.
        rj = response.json()
        _emit_llm_call(model_id, url, messages, rj.get("usage"))
        return rj["choices"][0]["message"]

    # All providers in this attempt 429'd or errored. Honor a short sleep
    # then retry the chain once — usually one provider's cooldown will
    # have expired by then.
    wait = 30.0
    print(f"[call_llm] all providers cooled; sleeping {wait:.0f}s and retrying once", flush=True)
    time.sleep(wait)
    for url, key, model_id in _providers(model):
        if _budget_blocks_model(model_id):
            last_err = f"daily budget exhausted; skipping paid model {model_id}"
            try:
                events.emit(
                    "budget_blocked",
                    name=model_id,
                    model=model_id,
                    host=_url_host(url),
                    result=last_err,
                )
            except Exception:
                pass
            continue
        payload = {
            "model": model_id,
            "messages": _maybe_add_cache_control(
                _strip_internal_fields(messages), url,
            ),
        }
        if tool_schemas is not None:
            payload["tools"] = tool_schemas
            payload["tool_choice"] = "auto"
            payload["parallel_tool_calls"] = False
        response = httpx.post(
            url,
            headers={**_HTTP_HEADERS_BASE, "Authorization": f"Bearer {key}"},
            json=payload,
            timeout=60.0,
        )
        if response.status_code == 429:
            _cool_provider(url, model_id)
            continue
        if _is_transient_provider_error(response):
            last_err = response.text
            _cool_provider(url, model_id, PROVIDER_UNAVAILABLE_COOLDOWN_SECONDS)
            continue
        if response.status_code >= 400:
            raise RuntimeError(f"API error {response.status_code}: {response.text}")
        rj = response.json()
        _emit_llm_call(model_id, url, messages, rj.get("usage"))
        return rj["choices"][0]["message"]

    raise RuntimeError(f"All providers exhausted: {last_err}")


def _url_host(url: str) -> str:
    """Extract the host part of a URL for compact event labels."""
    try:
        return url.split("://", 1)[1].split("/", 1)[0]
    except (IndexError, AttributeError):
        return url


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
            try:
                events.emit(
                    "budget_blocked",
                    name=model_id,
                    model=model_id,
                    host=_url_host(url),
                    result=last_err,
                )
            except Exception:
                pass
            continue
        payload: dict[str, Any] = {
            "model": model_id,
            "messages": _maybe_add_cache_control(
                _strip_internal_fields(messages), url,
            ),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tool_schemas is not None:
            payload["tools"] = tool_schemas
            payload["tool_choice"] = "auto"
            payload["parallel_tool_calls"] = False
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
            wait_for = retry_after if (retry_after is not None) else PRIMARY_DEFAULT_RETRY_WAIT
            if idx == 0 and wait_for <= PRIMARY_MAX_RETRY_WAIT:
                response_ctx.__exit__(None, None, None)
                response_ctx = None
                response = None
                print(f"[call_llm_stream] {model_id} 429, waiting {wait_for:.0f}s — retrying primary", flush=True)
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
                cool_for = retry_after if retry_after else PROVIDER_COOLDOWN_SECONDS
                _cool_provider(url, model_id, cool_for)
                print(f"[call_llm_stream] {model_id} 429 → cooling {cool_for:.0f}s, trying next", flush=True)
                continue
            # No retry was attempted (not primary, or retry_after too long) —
            # close the original 429 stream and cool this provider.
            cool_for = retry_after if retry_after else PROVIDER_COOLDOWN_SECONDS
            response_ctx.__exit__(None, None, None)
            response_ctx = None
            response = None
            _cool_provider(url, model_id, cool_for)
            print(f"[call_llm_stream] {model_id} 429 → cooling {cool_for:.0f}s, trying next", flush=True)
            continue
        if _is_transient_provider_error(response):
            response.read()
            last_err = response.text
            _status = response.status_code
            response_ctx.__exit__(None, None, None)
            response_ctx = None
            response = None
            _cool_provider(url, model_id, PROVIDER_UNAVAILABLE_COOLDOWN_SECONDS)
            print(
                f"[call_llm_stream] {model_id} unavailable ({_status}) "
                "→ cooling 10m, trying next",
                flush=True,
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
        raise RuntimeError(f"All providers exhausted: {last_err}")

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
                print(
                    f"[stream] tool {slot['function'].get('name','?')} args "
                    f"didn't parse, defaulting to {{}}: {args[:120]!r}",
                    flush=True,
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


# --- Tool argument validation ---------------------------------------------

def _validate_tool_args(name: str, arguments: dict) -> str | None:
    """Schema-validate tool arguments before dispatch.

    Returns an error string if validation fails, None if clean.
    Checks: required fields present, primitive types match.
    Does NOT do deep recursive validation — the goal is catching the most
    common LLM mistake (missing required arg, wrong type) cheaply.
    """
    # tools.SCHEMAS is a live proxy; iterate each call so we always see
    # the current schema set even after a hot-reload.
    schema = None
    for s in tools.SCHEMAS:
        if s.get("function", {}).get("name") == name:
            schema = s
            break
    if schema is None:
        known = sorted(s.get("function", {}).get("name", "?") for s in tools.SCHEMAS)
        return (
            f"tool '{name}' does not exist. "
            f"Available tools: {', '.join(known)}. "
            "Retry using one of those exact names."
        )

    params = schema.get("function", {}).get("parameters", {})
    required = params.get("required") or []
    properties = params.get("properties") or {}

    missing = [k for k in required if k not in arguments]
    if missing:
        return f"missing required argument(s): {', '.join(missing)}"

    errors: list[str] = []
    type_map = {
        "string": str,
        "boolean": bool,
        "integer": int,
        "number": (int, float),
        "array": list,
        "object": dict,
    }
    for key, value in arguments.items():
        prop = properties.get(key)
        if prop is None:
            continue
        expected = prop.get("type")
        py_type = type_map.get(expected)
        if py_type and not isinstance(value, py_type):
            errors.append(f"'{key}' must be {expected}, got {type(value).__name__}")
            continue
        enum_vals = prop.get("enum")
        if enum_vals is not None and value not in enum_vals:
            errors.append(f"'{key}' must be one of {enum_vals!r}, got {value!r}")

    return "; ".join(errors) if errors else None


# --- The agent ------------------------------------------------------------

class Agent:
    """A single conversational agent with tool-use capability.

    Holds the message history, so multiple .chat() calls build on each
    other (the agent remembers what you said earlier in the session).
    """

    def __init__(
        self,
        memory: Memory | None = None,
        system_prompt: str = SYSTEM_PROMPT,
        model: str | None = None,
    ) -> None:
        self.memory = memory
        self.model = model or MODEL
        # Per-session active tool set. Starts with the always-loaded
        # core; the agent grows it by calling load_tool(name). Lets
        # us send ~1K tokens of schemas per call instead of ~5K when
        # most tools aren't needed for the current turn. The stub
        # `tools` module used by tests has no ALWAYS_LOADED — fall
        # back to None, which _run_loop interprets as "send all
        # schemas" (preserving legacy behaviour for those tests).
        self._active_tool_names: set[str] | None = (
            set(tools.ALWAYS_LOADED)
            if hasattr(tools, "ALWAYS_LOADED")
            else None
        )
        full_prompt = system_prompt
        # AGENTS.md is now loaded lazily by _current_system_prompt so
        # edits to the file take effect on the next turn without a
        # restart. The cache key is (path, mtime) — re-read only when
        # the file changes on disk.
        self._agents_md_cache: tuple[str, float, str] | None = None
        if memory is not None:
            # Pinned core block: user profile + key feedback rules (full bodies,
            # capped small). Always in context so the agent knows who it's
            # talking to without needing a recall() call every turn.
            core_block = memory.load_core_block()
            if core_block:
                full_prompt += "\n\n# Pinned facts (user profile + key rules)\n\n" + core_block
            # Index: one-line-per-entry so the agent knows what memories exist.
            # Full bodies fetched on demand via recall(query).
            full_prompt += "\n\n# Memory index\n\n" + memory.load_index(max_entries=8)
        self._base_system_prompt = full_prompt
        self.history: list[dict] = [{"role": "system", "content": full_prompt}]

    def reset(self) -> None:
        """Wipe history except for the system prompt.

        Also clears any saved session on disk, so a future restart
        doesn't restore the cleared turns.
        """
        self.history = self.history[:1]
        if self.memory is not None:
            self.memory.clear_session()
            self.memory.clear_world_state()

    def restore_session(self) -> int:
        """Opt-in: load previously-saved conversation history.

        Callers that want cross-session continuity (REPL, Telegram bot)
        call this right after __init__. Callers that want fresh-each-
        time semantics (heartbeat) simply don't call it.

        Returns the number of messages restored (0 if no saved session).
        """
        if self.memory is None:
            return 0
        saved = self.memory.load_session()
        self.history.extend(saved)
        return len(saved)

    def reflect(self) -> str:
        """Ask the LLM to review the conversation and save anything worth
        remembering. Called at session end."""
        return self.chat(
            "We're ending this session. Reflect on our conversation: are "
            "there any durable facts about me, ongoing work, preferences, "
            "or references worth remembering for future sessions? For each "
            "one, call remember() with an appropriate type. Skip anything "
            "ephemeral or already covered by existing memory. If nothing is "
            "worth saving, just say so in one line."
        )

    def chat(self, user_message: str, source: str = "web") -> str:
        """Send a user message; return the agent's final text reply.

        `source` tags which channel the message arrived from
        ("web" / "telegram" / "repl" / "heartbeat") so the unified
        chat log can show provenance. Default is "web" since most
        callers are the web UI.
        """
        return "".join(self._run_loop(user_message, streaming=False, source=source))

    def chat_stream(self, user_message: str, source: str = "web"):
        """Streaming variant of chat() for the web UI.

        Yields content strings as they arrive from the LLM. Tool calls
        happen silently — their activity is visible via the /events SSE
        feed. This is a sync generator; FastAPI is happy to consume it.
        """
        yield from self._run_loop(user_message, streaming=True, source=source)

    def _load_agents_md_cached(self) -> str:
        """Read AGENTS.md, cached by (path, mtime). Returns '' if missing.

        Refreshing per-turn means edits to the user-owned persona file
        land on the next agent turn without a service restart. The
        cache prevents re-reading the same content every turn.
        """
        path = _resolve_agents_md()
        if path is None:
            self._agents_md_cache = None
            return ""
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return self._agents_md_cache[2] if self._agents_md_cache else ""
        key = (str(path), mtime)
        if (
            self._agents_md_cache is not None
            and (self._agents_md_cache[0], self._agents_md_cache[1]) == key
        ):
            return self._agents_md_cache[2]
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            return self._agents_md_cache[2] if self._agents_md_cache else ""
        self._agents_md_cache = (str(path), mtime, content)
        return content

    def _current_system_prompt(self) -> str:
        """Build the system prompt for this turn.

        STRUCTURE FOR PROVIDER CACHE HIT RATE — order matters here.

        Gemini 2.5's implicit cache + OpenRouter/Anthropic explicit cache
        both prefix-match. ANY change to bytes earlier in the prompt
        invalidates the cache for everything after. So we layer:

            [stable]    base system prompt
            [stable]    AGENTS.md (mtime-cached)
            [stable]    loadable tools catalogue
            ──── implicit cache boundary ────
            [volatile]  current date/time (changes every minute)
            [volatile]  session world state (changes every turn)
            [sporadic]  rate-limit signal (only when a provider just cooled)

        Pre-reorder we were appending the date line right after the base
        prompt, which invalidated the cache for AGENTS.md + everything
        downstream on every turn. Hit rate measured at ~40% on Gemini;
        target after this change is 70-80%+.
        """
        # ── STABLE PREFIX (cacheable) ────────────────────────────────
        prompt = self._base_system_prompt

        # AGENTS.md hot-reload. Lazy + mtime-cached so unchanged files
        # don't hit the disk on every turn. Lets the user edit AGENTS.md
        # and see persona changes on the next turn without `docker restart`.
        agents_md = self._load_agents_md_cached()
        if agents_md:
            prompt += "\n\n# Identity (AGENTS.md — user-owned persona)\n\n" + agents_md

        # Loadable tool catalogue. Stays stable as long as the active
        # set doesn't change — which it usually doesn't within a single
        # task run. Belongs in the cacheable prefix.
        loadable = (
            tools.tool_overview(exclude=self._active_tool_names)
            if self._active_tool_names is not None
            and hasattr(tools, "tool_overview")
            else []
        )
        if loadable:
            lines = ["", "# Loadable tools (call load_tool('name') to enable)", ""]
            for row in loadable:
                desc = row["description"].split("\n", 1)[0][:120]
                lines.append(f"- `{row['name']}` — {desc}")
            prompt += "\n".join(lines)

        # ── VOLATILE SUFFIX (re-rendered every turn) ─────────────────
        tz_name = os.environ.get("TZ", "Asia/Kolkata")
        try:
            tz = ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            tz = ZoneInfo("UTC")
        now = datetime.now(tz=tz)
        date_line = now.strftime("Current date/time: %A, %Y-%m-%d %H:%M %Z")
        prompt += f"\n\n{date_line}"

        if self.memory is not None:
            state = self.memory.get_world_state()
            if state:
                prompt += "\n\n# Session world state\n\n" + json.dumps(state, indent=2)

        # Recent rate-limit signal. When a provider just cooled in the
        # last 2 minutes, nudge the agent to checkpoint / continue_task
        # rather than blindly retry — otherwise it'll burn through every
        # fallback in the chain and trigger the silent-drop path.
        recent_cool = _recent_provider_cool_seconds()
        if recent_cool is not None and recent_cool < 120:
            prompt += (
                f"\n\n# Heads up\n\nA provider just rate-limited "
                f"{int(recent_cool)}s ago. Fallback providers will serve "
                f"the next ~{max(60 - int(recent_cool), 10)}s and they're "
                f"slower/weaker. If you're mid-task, prefer "
                f"continue_task() with a scratchpad note over burning "
                f"the rest of your iteration budget on retries."
            )

        return prompt

    def _run_loop(self, user_message: str, streaming: bool, source: str = "web"):
        """Unified agent loop generator shared by chat() and chat_stream().

        Structural improvements over the prior dual-path design:
          - Single code path: bug fixes land for both modes at once.
          - Output guard: validates each final reply before it reaches
            the user; catches leaked internal paths, error echoes, and
            confabulation from ungrounded tool calls.
          - Tool-arg validation: schema-checks LLM arguments before
            dispatching; hands a structured error back to the LLM so it
            can correct and retry rather than crashing the loop.
          - No auto memory injection: the agent calls recall(query)
            explicitly instead of receiving fuzzy keyword matches it
            didn't ask for. Eliminates the main confabulation vector.

        Yields:
          streaming=True  → content chunks in real-time as LLM produces
          streaming=False → a single string (the final guarded reply)
        """
        local = self._handle_local_command(user_message)
        if local is not None:
            if self.memory is not None:
                self.memory.log_turn("user", user_message)
                self.memory.log_turn("assistant", local)
            events.emit("user_message", text=events.full_text(user_message))
            events.emit("assistant_reply", text=events.full_text(local))
            yield local
            return

        # Refresh the system prompt with the current date/time each turn so
        # the agent always has accurate temporal context (e.g. for scheduling).
        self.history[0]["content"] = self._current_system_prompt()

        evicted = _evict_prior_tool_results(self.history)
        if evicted:
            try:
                events.emit(
                    "tool_results_evicted",
                    text=events.truncate_preview(
                        f"stubbed {evicted} tool result(s) from prior turns"
                    ),
                )
            except Exception:
                pass

        self._maybe_compact()
        self.history.append({
            "role": "user",
            "content": user_message,
            "source": source,
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
        if self.memory is not None:
            self.memory.log_turn("user", user_message)
            # Auto-stamp world state at turn start so resume after restart
            # always has a current focus, regardless of whether the LLM
            # chooses to call update_world_state itself.
            self.memory.update_world_state({
                "focus": user_message[:120],
                "step": 0,
                "last_action": None,
                "last_ok": None,
            })
        events.emit("user_message", text=events.full_text(user_message))

        tool_names_used: set[str] = set()
        call_counts: dict[tuple[str, str], int] = {}
        # Per-turn cache for READ_ONLY_CACHEABLE_TOOLS — same (name, args) → same
        # result, no need to re-execute. Cleared at the start of every turn.
        tool_result_cache: dict[tuple[str, str], str] = {}
        # Per-turn ledger of tool outcomes used by the output guard's
        # claim-consistency check. Records (name, args, success) for every
        # tool call so we can answer "did the agent actually succeed at the
        # action it's now claiming to have done?"
        tool_outcomes: list[dict] = []

        for _turn_idx in range(MAX_TURNS):
            # Mid-loop eviction: keep only the two most recent tool results
            # full-fidelity; stub everything older. Without this, per-call
            # input grows linearly with iteration count — a 15-iter task
            # that does several web_fetches can balloon to 90K+ of stale
            # payloads on every LLM call, hitting free-tier TPM caps. The
            # last two are enough for the agent to reason over the call(s)
            # it just made; anything older has already been condensed into
            # the assistant's prior decision.
            if _turn_idx > 0:
                in_loop_evicted = _evict_prior_tool_results(self.history, keep_recent=2)
                if in_loop_evicted:
                    try:
                        events.emit(
                            "tool_results_evicted",
                            text=events.truncate_preview(
                                f"in-loop: stubbed {in_loop_evicted} older "
                                f"tool result(s) at iter {_turn_idx + 1}"
                            ),
                        )
                    except Exception:
                        pass

            # Item 5: pre-turn hook. Lets a caller (heartbeat TaskGuard, tests)
            # inject a synthetic user message at the start of any iteration.
            # The TaskGuard uses this at iter MAX_TURNS-1 to force a forced
            # complete_task call when any due task is still unfinished —
            # complementing the iter-(MAX_TURNS-2) budget nudge below.
            if tools._pre_turn_hook is not None:
                try:
                    injected = tools._pre_turn_hook(_turn_idx, self.history)
                except Exception as _hook_err:
                    injected = None
                    events.emit(
                        "self_correction",
                        text=f"pre_turn_hook raised at iter {_turn_idx}: {_hook_err}",
                        result="hook ignored",
                    )
                if injected is not None:
                    self.history.append(injected)
                    events.emit(
                        "self_correction",
                        text=f"pre_turn_hook injection at iter {_turn_idx + 1}/{MAX_TURNS}",
                        result=str(injected.get("content", ""))[:100],
                    )

            # Mid-loop modifier re-injection (mem0's instruction-dilution fix).
            # At the halfway point the original user message is buried under
            # ~10 messages of tool calls + results — attention weight on the
            # original task drops as it moves toward the middle of context.
            # Restating it as a fresh system note brings it back to high-
            # attention position. We also list which tools have been used so
            # the agent doesn't re-do work already completed.
            if _turn_idx == MAX_TURNS // 2 and user_message:
                used_summary = (
                    f" Tools used so far: {', '.join(sorted(tool_names_used))}."
                    if tool_names_used
                    else " No tools called yet."
                )
                # First 500 chars of the original task — enough for the
                # framing, not so much that we re-bloat the context.
                task_snippet = user_message[:500]
                if len(user_message) > 500:
                    task_snippet += "…"
                self.history.append({
                    "role": "system",
                    "content": (
                        f"# Reminder of the current goal\n\n"
                        f"You are still working on this request:\n\n"
                        f"> {task_snippet}\n\n"
                        f"{used_summary} You have {MAX_TURNS - _turn_idx} "
                        f"iterations left."
                    ),
                })
                events.emit(
                    "self_correction",
                    text=f"goal re-injection at iter {_turn_idx + 1}/{MAX_TURNS}",
                    result=task_snippet[:80],
                )

            # Budget nudge: 2 iterations before the hard cap, inject a synthetic
            # harness message reminding the model to wrap up. Without this the
            # loop silently hits MAX_TURNS and bails with a fallback string,
            # leaving any due heartbeat task stuck in `executing=True` (see the
            # post-success check in heartbeat.py:tick which then has to clean
            # up after the fact). The nudge gives the model a chance to call
            # complete_task / record_failure before the hard cap fires.
            if _turn_idx == MAX_TURNS - 2:
                self.history.append({
                    "role": "user",
                    "content": (
                        "Heads-up from the harness: you have 2 iterations left "
                        f"of a {MAX_TURNS}-step budget. If a task is still "
                        "active, call complete_task() now with what you have "
                        "(it's better to deliver a partial answer than nothing). "
                        "If the task cannot be completed, briefly explain why "
                        "and stop calling tools — the harness will record a "
                        "failure with your reasoning."
                    ),
                })
                events.emit(
                    "self_correction",
                    text=f"harness budget nudge at iter {_turn_idx + 1}/{MAX_TURNS}",
                    result="injected wrap-up reminder",
                )

            if streaming:
                # Buffer the full stream before yielding — lets the output
                # guard check the complete reply and self-correct if needed
                # before anything reaches the client.
                assistant_msg = None
                stream_chunks: list[str] = []
                active_schemas = (
                    tools.SCHEMAS
                    if self._active_tool_names is None
                    or not hasattr(tools, "schemas_for")
                    else tools.schemas_for(self._active_tool_names)
                )
                for kind, payload in call_llm_stream(
                    self.history, active_schemas, model=self.model
                ):
                    if kind == "content":
                        stream_chunks.append(payload)
                    elif kind == "done":
                        assistant_msg = payload
                if assistant_msg is None:
                    yield "\n(empty stream)\n"
                    return
            else:
                active_schemas = (
                    tools.SCHEMAS
                    if self._active_tool_names is None
                    or not hasattr(tools, "schemas_for")
                    else tools.schemas_for(self._active_tool_names)
                )
                assistant_msg = call_llm(self.history, active_schemas, model=self.model)

            # Strip provider-specific extras (reasoning, null fields) that
            # the API rejects when replayed as part of the next request.
            cleaned: dict[str, Any] = {"role": "assistant"}
            if assistant_msg.get("content"):
                cleaned["content"] = assistant_msg["content"]
            if assistant_msg.get("tool_calls"):
                cleaned["tool_calls"] = assistant_msg["tool_calls"]
            if "content" not in cleaned and "tool_calls" not in cleaned:
                cleaned["content"] = ""
            # Inherit source from the user turn so the unified chat log
            # can tag the reply to the channel it went out on.
            cleaned["source"] = source
            cleaned["ts"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self.history.append(cleaned)

            tool_calls = assistant_msg.get("tool_calls")
            if not tool_calls:
                raw_reply = assistant_msg.get("content") or ""
                if not raw_reply:
                    # LLM returned empty content with no tool calls — nudge
                    # it once (Letta empty-response recovery pattern).
                    raw_reply = self._nudge_for_reply()
                raw_reply = raw_reply or "(I'm not sure how to respond — could you rephrase?)"
                clean, violations = self._output_guard(raw_reply, tool_names_used, tool_outcomes)

                if clean is None:
                    # Guard fired — self-correct (AutoGen/Letta pattern).
                    events.emit(
                        "self_correction",
                        text=f"violations: {', '.join(violations)}",
                        result=raw_reply[:80].replace("\n", " "),
                    )
                    if "action_claim_without_tool_call" in violations:
                        # Model claimed to do something without calling tools.
                        # Re-enter the loop with a correction injected so the
                        # model can actually call the tool this time.
                        self.history.append({
                            "role": "user",
                            "content": self._ACTION_CLAIM_CORRECTION_PROMPT,
                        })
                        continue  # next iteration picks up the correction
                    reply = self._self_correct(tool_names_used, violations, tool_outcomes)
                    self.history[-1]["content"] = reply
                else:
                    reply = clean
                    self.history[-1]["content"] = reply

                if streaming:
                    # Stream was buffered — now flush the final reply.
                    yield reply
                else:
                    yield reply

                if self.memory is not None:
                    self.memory.log_turn("assistant", reply)
                events.emit("assistant_reply", text=events.full_text(reply))
                return

            for call in tool_calls:
                name = call["function"]["name"]
                raw_args = call["function"].get("arguments") or "{}"
                try:
                    args = json.loads(raw_args)
                except json.JSONDecodeError:
                    args = {}

                tool_names_used.add(name)
                self._log_tool_call(name, args)
                events.emit(
                    "tool_call",
                    name=name,
                    args=events.truncate_preview(json.dumps(args, ensure_ascii=False), limit=800),
                )

                # Loop/stuck detection: same (tool, args) called 3+ times this
                # session signals the agent is looping. Inject a corrective result
                # instead of executing, so the LLM sees the warning and pivots.
                canon_args = json.dumps(args, sort_keys=True)
                call_key = (name, canon_args)
                call_counts[call_key] = call_counts.get(call_key, 0) + 1
                # Per-turn cache hit: read-only tools with the same args this
                # turn return the cached result with a hint. Saves wasted
                # round trips and prevents read_file/recall thrashing —
                # the most common shape of the "stuck loop" failure in
                # production was 3× read_file with identical args.
                if (
                    name in READ_ONLY_CACHEABLE_TOOLS
                    and call_key in tool_result_cache
                ):
                    cached = tool_result_cache[call_key]
                    result = (
                        f"(harness-cached result from earlier this turn — "
                        f"call #{call_counts[call_key]} for '{name}' with "
                        f"identical args)\n\n"
                        f"{cached}\n\n"
                        f"[Hint: you already called {name} with these arguments. "
                        f"No need to re-call — proceed with the next step.]"
                    )
                    events.emit(
                        "output_guard",
                        name=name,
                        text=f"cache hit: {name} × {call_counts[call_key]}",
                        result=canon_args[:120],
                    )
                elif call_counts[call_key] >= 3:
                    result = (
                        f"STUCK_LOOP: '{name}' has been called with these exact arguments "
                        f"{call_counts[call_key]} times this session. You are in a loop. "
                        "Stop, reason about why the previous calls did not achieve the goal, "
                        "and try a fundamentally different approach or ask the user for help."
                    )
                    events.emit(
                        "output_guard",
                        name=name,
                        text=f"stuck loop: {name} × {call_counts[call_key]}",
                        result=canon_args[:120],
                    )
                # Schema-validate args before dispatch. On failure the LLM
                # gets a structured error and can correct + retry rather
                # than running the tool with garbage arguments.
                elif validation_error := _validate_tool_args(name, args):
                    result = (
                        f"ERROR: invalid arguments for '{name}': {validation_error}. "
                        f"Check the tool schema and retry with corrected arguments."
                    )
                else:
                    # Intercept load_tool BEFORE dispatching so the
                    # active set is updated for the next LLM call. The
                    # tool function itself just returns a confirmation
                    # string; the side effect is here on the Agent.
                    if name == "load_tool" and self._active_tool_names is not None:
                        requested = (args or {}).get("name", "").strip()
                        known = (
                            tools.tool_names()
                            if hasattr(tools, "tool_names")
                            else set()
                        )
                        if requested and requested in known:
                            self._active_tool_names.add(requested)
                    _t_start = time.monotonic()
                    result = tools.execute(name, args)
                    _t_duration_ms = int((time.monotonic() - _t_start) * 1000)
                    # Emit a follow-up tool_call_duration event so the
                    # dashboard can surface slow tools. Cheap — only the
                    # tool name + ms; the call itself was already logged.
                    try:
                        events.emit(
                            "tool_call_duration",
                            name=name,
                            text=f"{_t_duration_ms}ms",
                            result=str(_t_duration_ms),
                        )
                    except Exception:
                        pass
                    # Populate the per-turn cache for read-only tools.
                    # Only cache real (non-error) results so a failed call
                    # can still be retried with the same args.
                    if (
                        name in READ_ONLY_CACHEABLE_TOOLS
                        and isinstance(result, str)
                        and not result.startswith("ERROR")
                    ):
                        tool_result_cache[call_key] = result

                # Auto-update world state after every tool call so the agent
                # can resume correctly after a restart without relying on the
                # LLM to remember to call update_world_state itself.
                # Skip internal state tools to avoid infinite recursion.
                if self.memory is not None and name not in (
                    "get_world_state", "update_world_state"
                ):
                    step = call_counts.get(call_key, 1)
                    succeeded = not (
                        isinstance(result, str) and result.startswith("ERROR")
                    )
                    try:
                        self.memory.update_world_state({
                            "last_action": name,
                            "last_ok": succeeded,
                            "step": step,
                        })
                    except Exception:
                        pass  # never let state tracking break the agent loop

                events.emit(
                    "tool_result",
                    name=name,
                    result=events.truncate_preview(result, limit=2000),
                )
                # Record the outcome for the output guard's claim-consistency
                # check. We deliberately record raw args (not canonicalized)
                # so the path/URL is recoverable for regex matching.
                _outcome_success = not (
                    isinstance(result, str)
                    and (result.startswith("ERROR") or result.startswith("Error"))
                )
                tool_outcomes.append({
                    "name": name,
                    "args": args,
                    "success": _outcome_success,
                })
                # Trim oversized results before they enter history. The full
                # payload is preserved in the events log; the agent can
                # re-call the tool with a refined query if it needs more.
                self.history.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": _trim_tool_result_for_history(name, result),
                })

        fallback = "(hit MAX_TURNS without a final answer)"
        if not streaming:
            yield fallback
        else:
            yield f"\n{fallback}\n"

    def _output_guard(self, reply: str, tool_names_used: set[str], tool_outcomes: list[dict] | None = None) -> tuple[str | None, list[str]]:
        """Validate a final reply before it reaches the user.

        Catches deterministic failure modes:
          1. Memory filename leak — internal *.md paths in reply
          2. Internal path leak — workspace/memory/… strings in reply
          3. Error echo — LLM forwarded a tool ERROR string verbatim
          4. Example.com confabulation — placeholder site cited with no
             web tool active this turn
          5. Claim/result inconsistency — the reply claims to have read,
             fetched, written, or saved a specific path/URL, but the
             matching tool call in this turn returned an error. Catches
             hallucinated tool success (stress probe #22).

        Returns (reply, []) if clean, or (None, violations) if not.
        None signals the caller to attempt self-correction before
        falling back to a static error message.
        """
        violations: list[str] = []
        tool_outcomes = tool_outcomes or []

        # File-path guards are intentionally bypassed when the agent explicitly
        # searched or listed files — those results belong in the reply.
        file_search_active = bool(tool_names_used & {"search_files", "list_files"})

        if not file_search_active and _GUARD_MEMORY_FILENAME_RE.search(reply):
            violations.append("memory_filename_leak")

        if not file_search_active and any(p in reply for p in _GUARD_INTERNAL_PATHS):
            violations.append("internal_path_leak")

        if reply.lstrip().startswith("ERROR:") or "ERROR running " in reply:
            violations.append("error_echo")

        lower_reply = reply.lower().replace("\n", " ")
        if any(t in lower_reply for t in _GUARD_CONFABULATION_TERMS):
            if not (tool_names_used & {"web_fetch", "web_search"}):
                violations.append("example_com_confabulation")

        # Catch hallucinated actions: model claims to have done something
        # (created a task, sent a notification, etc.) without calling any
        # tools. Only fires when tools are registered (not a Q&A-only session).
        if not tool_names_used and tools.SCHEMAS:
            if any(phrase in lower_reply for phrase in _GUARD_ACTION_CLAIM_PHRASES):
                violations.append("action_claim_without_tool_call")

        # Claim/result inconsistency — the reply asserts a successful action
        # against a specific target (file path or URL) but the matching tool
        # call in this turn errored. Stress probe #22: agent called
        # read_file three times, all returned ENOENT, then replied "I found
        # and read /etc/secret_config.yaml". The check is conservative —
        # only fires when (a) a target is explicitly mentioned and (b) ALL
        # tool calls in this turn against that target failed.
        if tool_outcomes:
            inconsistencies = _claim_target_inconsistencies(reply, tool_outcomes)
            if inconsistencies:
                violations.append("claim_inconsistent_with_tool_result")

        if not violations:
            return reply, []

        try:
            events.emit(
                "output_guard",
                violations=",".join(violations),
                preview=reply[:100].replace("\n", " "),
            )
        except Exception:
            pass
        print(f"[output_guard] blocked reply ({violations}): {reply[:80]!r}", flush=True)
        return None, violations

    _SELF_CORRECTION_PROMPT = (
        "Your previous reply mentioned internal file paths or system error strings "
        "that should not be shown to the user. Please restate your answer using "
        "plain language only — no filenames, no *.md paths, no ERROR prefixes. "
        "Describe what you do in terms the user understands."
    )

    _NUDGE_PROMPT = (
        "You haven't replied to the user yet. "
        "Answer their question now in plain language — one or two sentences."
    )

    def _nudge_for_reply(self) -> str:
        """Retry after an empty response: inject a one-sentence nudge (no history pollution).

        Used when the LLM returns empty content with no tool calls — typically
        after a chain of tool results with no bridging text. The nudge is
        pruned from history after the retry so conversation stays clean.
        """
        self.history.append({"role": "user", "content": self._NUDGE_PROMPT})
        try:
            # No tool schemas — we only want a plain text reply here, not tool calls.
            # Sending schemas caused Groq to 400 ("tool call validation failed").
            nudged = call_llm(self.history, None, model=self.model)
            reply = nudged.get("content") or ""
        except Exception:
            reply = ""
        self.history.pop()  # prune the injected nudge
        return reply

    _ACTION_CLAIM_CORRECTION_PROMPT = (
        "You just said you performed an action (created a task, sent a notification, etc.) "
        "but you did NOT call any tools. That is a hallucination — you cannot perform actions "
        "through text alone. You MUST call the appropriate tool now to actually do what "
        "the user asked. Do not explain — just call the tool."
    )

    _CLAIM_INCONSISTENT_CORRECTION_PROMPT = (
        "Your previous reply claimed you successfully read, fetched, or wrote a file or URL, "
        "but the tool calls in this turn against that target ALL returned errors. Do not "
        "fabricate success. Restate honestly what you tried and what failed, naming the "
        "actual error. If the user needs the action attempted differently, say so — do not "
        "pretend it succeeded."
    )

    def _self_correct(self, tool_names_used: set[str], violations: list[str] | None = None, tool_outcomes: list[dict] | None = None) -> str:
        """Inject a correction prompt and re-call the LLM once (non-streaming).

        This is the AutoGen / Letta self-correction pattern: when the guard
        fires we tell the model *why* and ask it to rephrase, then prune the
        correction exchange from history so the conversation stays clean.

        Returns the corrected reply (or a safe static fallback on second failure).
        """
        if violations and "action_claim_without_tool_call" in violations:
            correction = self._ACTION_CLAIM_CORRECTION_PROMPT
        elif violations and "claim_inconsistent_with_tool_result" in violations:
            correction = self._CLAIM_INCONSISTENT_CORRECTION_PROMPT
        else:
            correction = self._SELF_CORRECTION_PROMPT
        self.history.append({
            "role": "user",
            "content": correction,
        })
        try:
            corrected_msg = call_llm(self.history, tools.SCHEMAS, model=self.model)
            corrected_reply = corrected_msg.get("content") or ""
        except Exception:
            corrected_reply = ""

        # Prune the injected correction turn — it's an implementation detail,
        # not a real user message. The final history will contain only the
        # (possibly corrected) assistant turn.
        self.history.pop()  # remove correction user msg

        if not corrected_reply:
            return "I can help with that — could you rephrase your question?"

        clean, _ = self._output_guard(corrected_reply, tool_names_used, tool_outcomes or [])
        if clean is not None:
            return clean

        # Second attempt also failed — return a neutral fallback rather than
        # looping. The system prompt rule should prevent getting here in practice.
        return "I can help with that — could you give me a bit more context?"

    @staticmethod
    def _log_tool_call(name: str, args: dict[str, Any]) -> None:
        preview = ", ".join(f"{k}={repr(v)[:40]}" for k, v in args.items())
        print(f"  -> {name}({preview})")

    def _handle_local_command(self, user_message: str) -> str | None:
        """Handle explicit local commands without an LLM call.

        This is intentionally small and conservative. It is command plumbing,
        not a natural-language classifier. Ambiguous text falls through to
        the normal agent loop.
        """
        text = user_message.strip()
        lowered = text.lower()

        if lowered in {"/help", "help commands"}:
            return (
                "Local commands:\n"
                "- /tasks [active|completed|cancelled|all]\n"
                "- /memory\n"
                "- /status\n"
                "- /help\n"
                "Everything else goes to the normal agent."
            )

        if lowered.startswith("/tasks") or lowered in {"list tasks", "list active tasks"}:
            parts = lowered.split()
            status = parts[1] if len(parts) > 1 and parts[0] == "/tasks" else "active"
            if status not in {"active", "completed", "cancelled", "all"}:
                return "Usage: /tasks [active|completed|cancelled|all]"
            return self._format_tasks(status)

        if lowered in {"/memory", "list memories", "list my memories"}:
            if self.memory is None:
                return "Memory is not initialized."
            return self.memory.load_index(max_entries=20)

        if lowered in {"/status", "status"}:
            return self._local_status()

        return None

    def _format_tasks(self, status: str) -> str:
        store = TaskStore(Path(os.environ.get("HOMUNCULUS_TASKS_DIR", "./tasks")))
        tasks = store.list(status)
        if not tasks:
            return f"No {status} tasks."
        lines = [f"{status.title()} tasks:"]
        for task in tasks:
            due = task.get("due_at") or "no due date"
            recurrence = task.get("recurrence", "none")
            lines.append(
                f"- `{task.get('id')}`: {task.get('title')} "
                f"(due: {due}, recurrence: {recurrence})"
            )
        return "\n".join(lines)

    def _local_status(self) -> str:
        store = TaskStore(Path(os.environ.get("HOMUNCULUS_TASKS_DIR", "./tasks")))
        active = len(store.list("active"))
        due = len(store.due())
        memories = 0
        if self.memory is not None:
            memories = len([
                p for p in self.memory.root.glob("*.md")
                if p.name not in {"MEMORY.md", "README.md"} and not p.name.startswith("_")
            ])
        return (
            "Homunculus status:\n"
            f"- model: `{self.model}`\n"
            f"- active tasks: {active}\n"
            f"- due tasks: {due}\n"
            f"- memory entries: {memories}"
        )

    # --- Mid-session compaction -----------------------------------------

    def _maybe_compact(self) -> None:
        """If history has too many *user turns*, replace older turns
        with a summary.

        Counting USER messages — not all messages — is important because
        a tool-heavy turn (one user message → assistant calls 5 tools →
        5 tool results) inflates raw message count by 10× without adding
        any new user context. Compacting on raw count meant 2-3 normal
        conversational turns triggered compaction and dropped recent
        context the user clearly cared about.

        Cuts at a user-message boundary so we never split a paired
        (assistant tool_call → tool result) sequence — the API rejects
        orphaned tool messages.
        """
        user_idxs = [i for i, m in enumerate(self.history) if m.get("role") == "user"]
        if len(user_idxs) <= COMPACT_TRIGGER:
            return
        if len(user_idxs) <= COMPACT_KEEP_RECENT:
            return  # nothing to summarize without dropping recent context

        cut_at = user_idxs[-COMPACT_KEEP_RECENT]
        # Slice [1:cut_at] = everything between system prompt and the
        # turn we're keeping.
        to_summarize = self.history[1:cut_at]
        if not to_summarize:
            return

        summary = self._summarize_messages(to_summarize)
        summary_msg = {
            "role": "system",
            "content": f"# Summary of earlier conversation\n\n{summary}",
        }
        # New history: [original system prompt, summary, recent turns]
        self.history = [self.history[0], summary_msg] + self.history[cut_at:]
        try:
            events.emit(
                "context_compacted",
                text=events.truncate_preview(
                    f"summarized {len(to_summarize)} older messages; "
                    f"history now {len(self.history)}"
                ),
            )
        except Exception:
            pass
        print(
            f"[compact] summarized {len(to_summarize)} old messages "
            f"→ history now {len(self.history)} messages",
            flush=True,
        )

    def _summarize_messages(self, messages: list[dict]) -> str:
        """Ask the LLM (no tools) to write a tight summary of older turns."""
        flat = "\n\n".join(_flatten_message_for_summary(m) for m in messages)
        prompt = [
            {
                "role": "system",
                "content": (
                    "Summarize the following conversation excerpt into 3-6 "
                    "tight sentences. Preserve: what the user asked for, "
                    "decisions made, important facts, pending tasks. Skip "
                    "small talk and meta-commentary. Use plain text."
                ),
            },
            {"role": "user", "content": flat},
        ]
        try:
            response = call_llm(prompt, tool_schemas=None, model=self.model)
            return response.get("content") or "(no summary returned)"
        except Exception as e:
            # If summarization fails, fall back to a degenerate "summary"
            # so the conversation can continue without an exception.
            return f"(automatic summary failed: {e}; older context dropped)"


def _flatten_message_for_summary(msg: dict) -> str:
    """Render a single history message as a line for the summary prompt."""
    role = msg.get("role", "?")
    if msg.get("content"):
        return f"[{role}]: {msg['content']}"
    if msg.get("tool_calls"):
        names = [tc["function"]["name"] for tc in msg["tool_calls"]]
        return f"[{role}]: (called tools: {', '.join(names)})"
    return f"[{role}]: (empty)"
