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
# Slot 2 (HOMUNCULUS_API_KEY_FALLBACK_2): OpenRouter — many free models,
#   ~20 RPM, ~200 RPD per model. Get key at openrouter.ai/keys.
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
    "HOMUNCULUS_MODEL_FALLBACK_2", "deepseek/deepseek-chat:free"
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

# Module-level cooldown cache: url -> wall-clock expiry timestamp.
# A provider is "cooled" if time.time() < its expiry.
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


SYSTEM_PROMPT = """You are Homunculus, a minimal autonomous personal assistant.

Working directory: your current directory IS already the workspace. Use
plain relative filenames like `summary.md` or `notes/today.md`. Do NOT
prefix paths with `workspace/` — that would create a nested
workspace/workspace/ folder by mistake. Memory lives at `memory/`,
daily logs at `memory/logs/YYYY/MM/YYYY-MM-DD.md` (these paths are
correct as-is).

You have these tools available:
- read_file(path): read a UTF-8 text file
- write_file(path, content): write text to a file (overwrites)
- shell_exec(command): run a shell command (user must approve each one)
- remember(name, description, type, body, related=None): save a durable fact to long-term memory. `related` is an optional list of memory slugs (e.g. ["user_role", "project_homunculus"]) when this memory naturally connects to existing ones — they render as Obsidian [[wikilinks]] so the graph view shows the relationship.
- forget(name): delete an outdated, contradicted, or irrelevant memory by name or filename. Use sparingly; see the "Memory hygiene" rules below.
- python(code): run Python code in a sandbox container (no network, 256MB cap, 30s timeout). Returns stdout/stderr. Use for math, parsing, computation, verification. Cannot persist files.
- web_search(query): search the web. Returns titles, URLs, snippets, and often an answer summary. Use for current/external info not in your memory. When you use results, ALWAYS cite the full URL (https://...), never just the positional index like "result 1" — positional refs are useless to the user.
- web_fetch(url): download a URL and return its main text. Use to read a full article after web_search identifies it.
- notify(text): push a message to the user via Telegram. INTERRUPTS them — use sparingly, only for time-sensitive things (a deadline tomorrow, a question that blocks progress). Routine summaries belong in files, not notifications.

Memory works like this: every session you receive a "Your memory" section
below containing an index of everything you've remembered before. Each
entry is a short link to a detail file, annotated with how old it is
("today", "3 days ago", etc.). Entries marked "may be stale" should be
double-checked against current code before you act on them. When an entry
looks relevant to the current task, use read_file to load its full body.

Conversation logs: every user message and your final reply are appended
to memory/logs/YYYY/MM/YYYY-MM-DD.md (append-only). During end-of-session
reflection, you may use read_file on recent log files to remind yourself
what happened before saving durable facts via remember(). Memory types:
- "user": facts about the user (role, expertise, preferences)
- "feedback": collaboration rules the user has set
- "project": ongoing work context
- "reference": pointers to external resources

Memory hygiene (important — keeps the index small and signal-rich):
1. BEFORE calling remember(), scan your memory index for an existing
   entry that covers the same fact. If one exists, call remember() with
   the SAME `name` as that entry — that overwrites it in place rather
   than creating a duplicate (`user_role` vs `user_role_2`).
2. If you encounter a memory that is contradicted by current reality
   or by a newer memory, call forget() on the outdated one. Don't
   leave conflicting entries — they confuse future you.
3. Before acting on a memory marked "⚠ may be stale", verify it
   against current state (file exists, fact is still true). If the
   memory is plainly wrong, forget() it. If still valid, just trust
   and proceed (the staleness flag is a hint, not a problem).
4. Be conservative with forget(). When in doubt, leave the memory
   alone — losing context is worse than carrying a slightly old fact.

Think step by step. Use a tool when you need information from disk or
the system; otherwise answer directly. When a task is complete, reply with
a short summary instead of calling another tool. Don't call tools you
don't need.
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
    raw = [
        (API_URL, os.environ.get("HOMUNCULUS_API_KEY", ""), model_override or MODEL),
        (API_URL_FALLBACK, os.environ.get("HOMUNCULUS_API_KEY_FALLBACK", ""), MODEL_FALLBACK),
        (API_URL_FALLBACK_2, os.environ.get("HOMUNCULUS_API_KEY_FALLBACK_2", ""), MODEL_FALLBACK_2),
        (API_URL_FALLBACK_3, os.environ.get("HOMUNCULUS_API_KEY_FALLBACK_3", ""), MODEL_FALLBACK_3),
    ]
    have_keys = [p for p in raw if p[1]]
    now = time.time()
    fresh = [p for p in have_keys if _PROVIDER_COOLDOWN.get(p[0], 0) <= now]
    return fresh or have_keys[:1]  # never return empty if any keys are set


def _cool_provider(url: str) -> None:
    """Mark a provider as 429'd; skip it for PROVIDER_COOLDOWN_SECONDS."""
    _PROVIDER_COOLDOWN[url] = time.time() + PROVIDER_COOLDOWN_SECONDS


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
            _cool_provider(url)
            continue

        if response.status_code == 429:
            last_err = response.text
            _cool_provider(url)
            print(f"[call_llm] {model_id} 429 → cooling 60s, trying next", flush=True)
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
            _cool_provider(url)
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
            _cool_provider(url)
            response_ctx = None
            response = None
            continue
        if response.status_code == 429:
            response.read()
            last_err = response.text
            response_ctx.__exit__(None, None, None)
            response_ctx = None
            response = None
            _cool_provider(url)
            print(f"[call_llm_stream] {model_id} 429 → cooling 60s, trying next", flush=True)
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
        # If memory is provided, paste the index into the system prompt so
        # the LLM sees what's already been remembered.
        full_prompt = system_prompt
        if memory is not None:
            full_prompt += "\n\n# Your memory\n\n" + memory.load_index()
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
        self._maybe_compact()  # bound history growth before adding more

        self.history.append({"role": "user", "content": user_message})
        if self.memory is not None:
            self.memory.log_turn("user", user_message)
        events.emit("user_message", text=events.truncate_preview(user_message))

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
        self._maybe_compact()
        self.history.append({"role": "user", "content": user_message})
        if self.memory is not None:
            self.memory.log_turn("user", user_message)
        events.emit("user_message", text=events.truncate_preview(user_message))

        final_reply_parts: list[str] = []

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

    @staticmethod
    def _log_tool_call(name: str, args: dict[str, Any]) -> None:
        """Print a short preview of each tool call as it happens, so the
        user can see what the agent is doing."""
        preview = ", ".join(f"{k}={repr(v)[:40]}" for k, v in args.items())
        print(f"  -> {name}({preview})")

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
