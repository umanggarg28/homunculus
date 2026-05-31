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
from datetime import datetime
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
# Slot 1 (HOMUNCULUS_API_KEY_FALLBACK): Groq — llama-3.3-70b-versatile has
#   strong tool use and instruction following. Good rate-limit backup.
# Slot 2 (HOMUNCULUS_API_KEY_FALLBACK_2): OpenRouter — free endpoints
#   can disappear or rate-limit upstream. We accept a comma-separated
#   list and try each model before moving to the next provider.
# Slot 3 (HOMUNCULUS_API_KEY_FALLBACK_3): Cerebras — fast inference,
#   generous TPM. Get key at cloud.cerebras.ai.
API_URL_FALLBACK = os.environ.get(
    "HOMUNCULUS_API_URL_FALLBACK",
    "https://api.groq.com/openai/v1/chat/completions",
)
MODEL_FALLBACK = os.environ.get("HOMUNCULUS_MODEL_FALLBACK", "llama-3.3-70b-versatile")

API_URL_FALLBACK_2 = os.environ.get(
    "HOMUNCULUS_API_URL_FALLBACK_2",
    "https://openrouter.ai/api/v1/chat/completions",
)
MODEL_FALLBACK_2 = os.environ.get(
    "HOMUNCULUS_MODEL_FALLBACK_2", "nvidia/nemotron-3-super-120b-a12b:free,google/gemma-4-31b-it:free"
)

API_URL_FALLBACK_3 = os.environ.get(
    "HOMUNCULUS_API_URL_FALLBACK_3",
    "https://api.cerebras.ai/v1/chat/completions",
)
MODEL_FALLBACK_3 = os.environ.get("HOMUNCULUS_MODEL_FALLBACK_3", "gpt-oss-120b")

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

# Module-level cooldown cache: url|model -> wall-clock expiry timestamp.
# A provider/model pair is "cooled" if time.time() < its expiry.
_PROVIDER_COOLDOWN: dict[str, float] = {}

# Hard cap on tool-use iterations per user turn. Without this, a broken
# LLM could call tools forever. 20 is plenty for any realistic task.
MAX_TURNS = 20

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
- For "every day at X" / "every week" / any RECURRING commitment, use
  create_task(recurrence="daily"|"weekly") — NEVER schedule_next_tick.
- schedule_next_tick is for one-shot wake timers only.

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


def _cool_provider(
    url: str,
    model_id: str,
    seconds: float = PROVIDER_COOLDOWN_SECONDS,
) -> None:
    """Mark a provider as temporarily unavailable; skip until expiry."""
    _PROVIDER_COOLDOWN[_provider_key(url, model_id)] = time.time() + seconds


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
        payload: dict[str, Any] = {"model": model_id, "messages": messages}
        if tool_schemas is not None:
            payload["tools"] = tool_schemas
            payload["tool_choice"] = "auto"
            payload["parallel_tool_calls"] = False

        try:
            response = httpx.post(
                url,
                headers={"Authorization": f"Bearer {key}"},
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
                        headers={"Authorization": f"Bearer {key}"},
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
        payload = {"model": model_id, "messages": messages}
        if tool_schemas is not None:
            payload["tools"] = tool_schemas
            payload["tool_choice"] = "auto"
            payload["parallel_tool_calls"] = False
        response = httpx.post(
            url,
            headers={"Authorization": f"Bearer {key}"},
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
        payload: dict[str, Any] = {
            "model": model_id,
            "messages": messages,
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
                headers={"Authorization": f"Bearer {key}"},
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
                        headers={"Authorization": f"Bearer {key}"},
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
        full_prompt = system_prompt
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

    def chat(self, user_message: str) -> str:
        """Send a user message; return the agent's final text reply."""
        return "".join(self._run_loop(user_message, streaming=False))

    def chat_stream(self, user_message: str):
        """Streaming variant of chat() for the web UI.

        Yields content strings as they arrive from the LLM. Tool calls
        happen silently — their activity is visible via the /events SSE
        feed. This is a sync generator; FastAPI is happy to consume it.
        """
        yield from self._run_loop(user_message, streaming=True)

    def _current_system_prompt(self) -> str:
        """Return the base system prompt with a fresh current-datetime line appended."""
        tz_name = os.environ.get("TZ", "Asia/Kolkata")
        try:
            tz = ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            tz = ZoneInfo("UTC")
        now = datetime.now(tz=tz)
        date_line = now.strftime("Current date/time: %A, %Y-%m-%d %H:%M %Z")
        return self._base_system_prompt + f"\n\n{date_line}"

    def _run_loop(self, user_message: str, streaming: bool):
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

        self._maybe_compact()
        self.history.append({"role": "user", "content": user_message})
        if self.memory is not None:
            self.memory.log_turn("user", user_message)
        events.emit("user_message", text=events.full_text(user_message))

        tool_names_used: set[str] = set()

        for _ in range(MAX_TURNS):
            if streaming:
                # Buffer the full stream before yielding — lets the output
                # guard check the complete reply and self-correct if needed
                # before anything reaches the client.
                assistant_msg = None
                stream_chunks: list[str] = []
                for kind, payload in call_llm_stream(
                    self.history, tools.SCHEMAS, model=self.model
                ):
                    if kind == "content":
                        stream_chunks.append(payload)
                    elif kind == "done":
                        assistant_msg = payload
                if assistant_msg is None:
                    yield "\n(empty stream)\n"
                    return
            else:
                assistant_msg = call_llm(self.history, tools.SCHEMAS, model=self.model)

            # Strip provider-specific extras (reasoning, null fields) that
            # the API rejects when replayed as part of the next request.
            cleaned: dict[str, Any] = {"role": "assistant"}
            if assistant_msg.get("content"):
                cleaned["content"] = assistant_msg["content"]
            if assistant_msg.get("tool_calls"):
                cleaned["tool_calls"] = assistant_msg["tool_calls"]
            if "content" not in cleaned and "tool_calls" not in cleaned:
                cleaned["content"] = ""
            self.history.append(cleaned)

            tool_calls = assistant_msg.get("tool_calls")
            if not tool_calls:
                raw_reply = assistant_msg.get("content") or ""
                if not raw_reply:
                    # LLM returned empty content with no tool calls — nudge
                    # it once (Letta empty-response recovery pattern).
                    raw_reply = self._nudge_for_reply()
                raw_reply = raw_reply or "(I'm not sure how to respond — could you rephrase?)"
                clean, violations = self._output_guard(raw_reply, tool_names_used)

                if clean is None:
                    # Guard fired — self-correct (AutoGen/Letta pattern).
                    events.emit(
                        "self_correction",
                        text=f"violations: {', '.join(violations)}",
                        result=raw_reply[:80].replace("\n", " "),
                    )
                    reply = self._self_correct(tool_names_used)
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

                # Schema-validate args before dispatch. On failure the LLM
                # gets a structured error and can correct + retry rather
                # than running the tool with garbage arguments.
                validation_error = _validate_tool_args(name, args)
                if validation_error:
                    result = (
                        f"ERROR: invalid arguments for '{name}': {validation_error}. "
                        f"Check the tool schema and retry with corrected arguments."
                    )
                else:
                    result = tools.execute(name, args)

                events.emit(
                    "tool_result",
                    name=name,
                    result=events.truncate_preview(result, limit=2000),
                )
                self.history.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": result,
                })

        fallback = "(hit MAX_TURNS without a final answer)"
        if not streaming:
            yield fallback
        else:
            yield f"\n{fallback}\n"

    def _output_guard(self, reply: str, tool_names_used: set[str]) -> tuple[str | None, list[str]]:
        """Validate a final reply before it reaches the user.

        Catches four failure modes deterministically:
          1. Memory filename leak — internal *.md paths in reply
          2. Internal path leak — workspace/memory/… strings in reply
          3. Error echo — LLM forwarded a tool ERROR string verbatim
          4. Example.com confabulation — placeholder site cited with no
             web tool active this turn

        Returns (reply, []) if clean, or (None, violations) if not.
        None signals the caller to attempt self-correction before
        falling back to a static error message.
        """
        violations: list[str] = []

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

    def _self_correct(self, tool_names_used: set[str]) -> str:
        """Inject a correction prompt and re-call the LLM once (non-streaming).

        This is the AutoGen / Letta self-correction pattern: when the guard
        fires we tell the model *why* and ask it to rephrase, then prune the
        correction exchange from history so the conversation stays clean.

        Returns the corrected reply (or a safe static fallback on second failure).
        """
        self.history.append({
            "role": "user",
            "content": self._SELF_CORRECTION_PROMPT,
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

        clean, _ = self._output_guard(corrected_reply, tool_names_used)
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
