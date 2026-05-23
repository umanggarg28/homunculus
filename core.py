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
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

import events
import tools
from memory import Memory
from tasks import TaskStore

# Load .env at module import so config reads below see its values. Safe
# to call twice (main.py also calls it) — load_dotenv won't overwrite
# env vars that are already set, e.g. by docker-compose's env_file.
load_dotenv(Path(__file__).parent / ".env")


# --- Config ---------------------------------------------------------------

# API endpoint and model are configurable via env vars so you can swap
# providers/models without code edits. Defaults target Groq's free tier
# with openai/gpt-oss-120b — the most reliable free tool-use model as
# of 2026. To experiment with others, set HOMUNCULUS_API_URL and/or
# HOMUNCULUS_MODEL in .env.
API_URL = os.environ.get(
    "HOMUNCULUS_API_URL",
    "https://api.groq.com/openai/v1/chat/completions",
)
MODEL = os.environ.get("HOMUNCULUS_MODEL", "openai/gpt-oss-120b")

# Fallback provider chain. Each slot is independent — set only the keys
# you have. On 429 from one provider, we move to the next; a 429'd
# provider is benched for PROVIDER_COOLDOWN_SECONDS so we don't hammer
# a throttled endpoint.
#
# Slot 1 (HOMUNCULUS_API_KEY_FALLBACK): Gemini's OpenAI-compatible
#   endpoint — 1M TPM free tier (vs Groq's 8K), but 15 RPM / 1500 RPD.
# Slot 2 (HOMUNCULUS_API_KEY_FALLBACK_2): OpenRouter — free endpoints
#   can disappear or rate-limit upstream. We accept a comma-separated
#   list and try each model before moving to the next provider.
# Slot 3 (HOMUNCULUS_API_KEY_FALLBACK_3): Cerebras — fast inference,
#   generous TPM. Get key at cloud.cerebras.ai.
API_URL_FALLBACK = os.environ.get(
    "HOMUNCULUS_API_URL_FALLBACK",
    "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
)
MODEL_FALLBACK = os.environ.get("HOMUNCULUS_MODEL_FALLBACK", "gemini-2.0-flash")

API_URL_FALLBACK_2 = os.environ.get(
    "HOMUNCULUS_API_URL_FALLBACK_2",
    "https://openrouter.ai/api/v1/chat/completions",
)
MODEL_FALLBACK_2 = os.environ.get(
    "HOMUNCULUS_MODEL_FALLBACK_2", "qwen/qwen3-coder:free,openrouter/free"
)

API_URL_FALLBACK_3 = os.environ.get(
    "HOMUNCULUS_API_URL_FALLBACK_3",
    "https://api.cerebras.ai/v1/chat/completions",
)
MODEL_FALLBACK_3 = os.environ.get("HOMUNCULUS_MODEL_FALLBACK_3", "llama-3.3-70b")

# How long to bench a provider after it returns 429. During this window
# we skip it entirely and route to the next provider in the chain. 60s
# is long enough for most rate-limit windows to refill (Groq TPM is
# per-minute, Gemini RPM is per-minute).
PROVIDER_COOLDOWN_SECONDS = 60.0
PROVIDER_UNAVAILABLE_COOLDOWN_SECONDS = 10 * 60.0

# Module-level cooldown cache: url|model -> wall-clock expiry timestamp.
# A provider/model pair is "cooled" if time.time() < its expiry.
_PROVIDER_COOLDOWN: dict[str, float] = {}

# Hard cap on tool-use iterations per user turn. Without this, a broken
# LLM could call tools forever. 20 is plenty for any realistic task.
MAX_TURNS = 20

# Mid-session compaction thresholds. When history grows past
# COMPACT_TRIGGER messages, we summarize all but the last
# COMPACT_KEEP_RECENT into a single system-role summary. This bounds
# context usage on long-running conversations (especially the Telegram
# bot which keeps state across days).
COMPACT_TRIGGER = 15
COMPACT_KEEP_RECENT = 6


SYSTEM_PROMPT = """You are Homunculus — an autonomous personal assistant with persistent memory.

Paths: your cwd IS the workspace. Use plain relative paths like `notes.md` or
`memory/logs/2026/05/2026-05-19.md`. Never prefix with `workspace/`.

Memory:
- Your MEMORY INDEX appears below. Each entry links to a file; read_file
  it when relevant. Entries marked "⚠ may be stale" — verify before
  acting on them.
- Before remember(), check if an entry already covers the fact. If so,
  reuse the same `name` to overwrite, don't create duplicates.
- If a memory is contradicted or obsolete, forget() it. Be conservative —
  carrying old facts is better than losing context.
- Types: user (about the user) · feedback (rules to follow) · project
  (active work) · reference (external pointers) · skill (procedures —
  how to do a thing, saved during reflection).

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

    OpenRouter free endpoints can disappear or be temporarily unavailable.
    Their 404 "No endpoints found..." is a routing condition, not a bug
    in the user request, so we bench that provider and try the next slot.
    """
    if response.status_code == 429:
        return True
    if response.status_code in {404, 502, 503, 504}:
        # `.text` on a streaming response that hasn't been .read() yet
        # raises ResponseNotRead. .read() is idempotent for buffered
        # responses, so it's safe to call unconditionally.
        try:
            response.read()
        except Exception:
            pass
        body = response.text.lower()
        return (
            "no endpoints found" in body
            or "no endpoint" in body
            or response.status_code in {502, 503, 504}
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
            _cool_provider(url, model_id)
            print(f"[call_llm] {model_id} 429 → cooling 60s, trying next", flush=True)
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
        events.emit("llm_call", model=model_id, host=_url_host(url))
        return response.json()["choices"][0]["message"]

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
        events.emit("llm_call", model=model_id, host=_url_host(url))
        return response.json()["choices"][0]["message"]

    raise RuntimeError(f"All providers exhausted: {last_err}")


def _url_host(url: str) -> str:
    """Extract the host part of a URL for compact event labels."""
    try:
        return url.split("://", 1)[1].split("/", 1)[0]
    except (IndexError, AttributeError):
        return url


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
    for url, key, model_id in _providers(model):
        payload: dict[str, Any] = {
            "model": model_id,
            "messages": messages,
            "stream": True,
        }
        if tool_schemas is not None:
            payload["tools"] = tool_schemas
            payload["tool_choice"] = "auto"
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
            response_ctx.__exit__(None, None, None)
            response_ctx = None
            response = None
            _cool_provider(url, model_id)
            print(f"[call_llm_stream] {model_id} 429 → cooling 60s, trying next", flush=True)
            continue
        if _is_transient_provider_error(response):
            response.read()
            last_err = response.text
            response_ctx.__exit__(None, None, None)
            response_ctx = None
            response = None
            _cool_provider(url, model_id, PROVIDER_UNAVAILABLE_COOLDOWN_SECONDS)
            print(
                f"[call_llm_stream] {model_id} unavailable ({response.status_code}) "
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
        events.emit("llm_call", model=model_id, host=_url_host(url))
        break

    if response_ctx is None or response is None:
        raise RuntimeError(f"All providers exhausted: {last_err}")

    content_acc: list[str] = []
    tool_calls_acc: dict[int, dict[str, Any]] = {}

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
            choices = chunk.get("choices") or []
            if not choices:
                continue
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
                if fn.get("name"):
                    slot["function"]["name"] += fn["name"]
                if fn.get("arguments"):
                    slot["function"]["arguments"] += fn["arguments"]
                yield ("tool_call_delta", slot)
    finally:
        response_ctx.__exit__(None, None, None)

    # Assemble the final assistant message in the shape the loop expects.
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
        # If memory is provided, paste a small index into the system prompt so
        # the LLM knows what memories exist. Full relevant snippets are
        # retrieved per user turn, without being saved into session history.
        full_prompt = system_prompt
        if memory is not None:
            full_prompt += "\n\n# Your memory index\n\n" + memory.load_index(max_entries=8)
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
        """Send a user message; return the agent's final text reply.

        Internally runs the tool-use loop: the LLM may call tools any
        number of times before producing a final answer. We cap at
        MAX_TURNS as a safety net.
        """
        local = self._handle_local_command(user_message)
        if local is not None:
            if self.memory is not None:
                self.memory.log_turn("user", user_message)
                self.memory.log_turn("assistant", local)
            events.emit("user_message", text=events.truncate_preview(user_message))
            events.emit("assistant_reply", text=events.truncate_preview(local))
            return local

        self._maybe_compact()  # bound history growth before adding more

        memory_context_index = self._inject_relevant_memory(user_message)
        self.history.append({"role": "user", "content": user_message})
        if self.memory is not None:
            self.memory.log_turn("user", user_message)
        events.emit("user_message", text=events.truncate_preview(user_message))

        try:
            for _ in range(MAX_TURNS):
                assistant_msg = call_llm(self.history, tools.SCHEMAS, model=self.model)
                # Keep only fields the API accepts on the request side. Groq
                # returns extras like `reasoning`, sometimes `executed_tools`,
                # and explicit `null` fields — replaying those triggers 400s.
                cleaned: dict[str, Any] = {"role": "assistant"}
                if assistant_msg.get("content"):
                    cleaned["content"] = assistant_msg["content"]
                if assistant_msg.get("tool_calls"):
                    cleaned["tool_calls"] = assistant_msg["tool_calls"]
                # An assistant message must have at least one of content/tool_calls.
                if "content" not in cleaned and "tool_calls" not in cleaned:
                    cleaned["content"] = ""
                self.history.append(cleaned)

                tool_calls = assistant_msg.get("tool_calls")
                if not tool_calls:
                    # No tool call → this is the final answer.
                    reply = assistant_msg.get("content") or "(empty response)"
                    if self.memory is not None:
                        self.memory.log_turn("assistant", reply)
                    events.emit("assistant_reply", text=events.truncate_preview(reply))
                    return reply

                # Otherwise: run each requested tool, append result, loop.
                for call in tool_calls:
                    name = call["function"]["name"]
                    args = json.loads(call["function"]["arguments"])
                    self._log_tool_call(name, args)
                    events.emit(
                        "tool_call",
                        name=name,
                        args=events.truncate_preview(json.dumps(args, ensure_ascii=False)),
                    )
                    result = tools.execute(name, args)
                    events.emit(
                        "tool_result",
                        name=name,
                        result=events.truncate_preview(result),
                    )
                    self.history.append({
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": result,
                    })

            return "(hit MAX_TURNS without a final answer)"
        finally:
            self._drop_message(memory_context_index)

    def chat_stream(self, user_message: str):
        """Streaming variant of chat() for the web UI.

        Yields strings (content chunks) as they arrive from the LLM.
        Tool calls happen silently in the background — their activity
        is observable via the /events SSE feed. After all turns are
        done, the final assembled reply is also stored in history and
        memory exactly like chat() does.

        This is a *sync* generator (httpx.stream is sync); FastAPI is
        happy to consume sync generators in StreamingResponse.
        """
        local = self._handle_local_command(user_message)
        if local is not None:
            if self.memory is not None:
                self.memory.log_turn("user", user_message)
                self.memory.log_turn("assistant", local)
            events.emit("user_message", text=events.truncate_preview(user_message))
            events.emit("assistant_reply", text=events.truncate_preview(local))
            yield local
            return

        self._maybe_compact()
        memory_context_index = self._inject_relevant_memory(user_message)
        self.history.append({"role": "user", "content": user_message})
        if self.memory is not None:
            self.memory.log_turn("user", user_message)
        events.emit("user_message", text=events.truncate_preview(user_message))

        final_reply_parts: list[str] = []

        try:
            for _ in range(MAX_TURNS):
                assistant_msg: dict[str, Any] | None = None
                for kind, payload in call_llm_stream(self.history, tools.SCHEMAS, model=self.model):
                    if kind == "content":
                        final_reply_parts.append(payload)
                        yield payload
                    elif kind == "done":
                        assistant_msg = payload
                    # tool_call_delta is accumulated inside call_llm_stream;
                    # we don't need to act on it per-chunk here.

                if assistant_msg is None:
                    yield "\n(empty stream)\n"
                    return

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
                    reply = assistant_msg.get("content") or "(empty response)"
                    if self.memory is not None:
                        self.memory.log_turn("assistant", reply)
                    events.emit("assistant_reply", text=events.truncate_preview(reply))
                    return

                for call in tool_calls:
                    name = call["function"]["name"]
                    args = json.loads(call["function"]["arguments"] or "{}")
                    self._log_tool_call(name, args)
                    events.emit(
                        "tool_call",
                        name=name,
                        args=events.truncate_preview(json.dumps(args, ensure_ascii=False)),
                    )
                    result = tools.execute(name, args)
                    events.emit(
                        "tool_result",
                        name=name,
                        result=events.truncate_preview(result),
                    )
                    self.history.append({
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": result,
                    })
                # Loop — next turn starts a fresh stream.
                # Reset content accumulator for next turn's text (if any).
                final_reply_parts = []

            yield "\n(hit MAX_TURNS without a final answer)\n"
        finally:
            self._drop_message(memory_context_index)

    @staticmethod
    def _log_tool_call(name: str, args: dict[str, Any]) -> None:
        """Print a short preview of each tool call as it happens, so the
        user can see what the agent is doing."""
        preview = ", ".join(f"{k}={repr(v)[:40]}" for k, v in args.items())
        print(f"  -> {name}({preview})")

    def _inject_relevant_memory(self, user_message: str) -> int | None:
        """Temporarily add relevant memory snippets for this turn only."""
        if self.memory is None:
            return None
        snippets = self.memory.search(user_message, limit=3, max_chars=900)
        if not snippets:
            return None
        msg = {
            "role": "system",
            "content": (
                "# Relevant memory snippets for this turn\n\n"
                f"{snippets}\n\n"
                "Use these if relevant. If they conflict with the current "
                "user message or current files, trust the newer/current state."
            ),
        }
        self.history.append(msg)
        return len(self.history) - 1

    def _drop_message(self, index: int | None) -> None:
        """Remove a temporary history message if it is still present."""
        if index is None:
            return
        if 0 <= index < len(self.history):
            del self.history[index]

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
        """If history is too long, replace its older portion with a summary.

        Cuts at a user-message boundary so we never split a paired
        (assistant tool_call → tool result) sequence — the API rejects
        orphaned tool messages.
        """
        if len(self.history) < COMPACT_TRIGGER:
            return

        # User-message indices in history (the system prompt is at [0]).
        user_idxs = [i for i, m in enumerate(self.history) if m.get("role") == "user"]
        if len(user_idxs) <= COMPACT_KEEP_RECENT:
            return  # already small in terms of conversation turns

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
