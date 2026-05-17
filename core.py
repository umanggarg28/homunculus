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

# Hard cap on tool-use iterations per user turn. Without this, a broken
# LLM could call tools forever. 20 is plenty for any realistic task.
MAX_TURNS = 20


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
- remember(name, description, type, body): save a durable fact to long-term memory

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

Think step by step. Use a tool when you need information from disk or
the system; otherwise answer directly. When a task is complete, reply with
a short summary instead of calling another tool. Don't call tools you
don't need.
"""


# --- HTTP layer -----------------------------------------------------------

def call_llm(messages: list[dict], tool_schemas: list[dict]) -> dict:
    """One round-trip to the Groq chat completions endpoint.

    Returns the assistant message dict, shape:
      {"role": "assistant",
       "content": str | None,
       "tool_calls": [...] | None}

    If "tool_calls" is present, the LLM is asking us to run tools.
    If "content" is present (and no tool_calls), it's a final answer.

    Retries once on 429 (rate limited). Groq sends a `retry-after` header
    telling us when capacity returns; we sleep that long + 1s buffer and
    try again. After one retry it gives up so we don't block forever.
    """
    api_key = os.environ.get("HOMUNCULUS_API_KEY")
    if not api_key:
        raise RuntimeError("HOMUNCULUS_API_KEY is not set.")

    payload = {
        "model": MODEL,
        "messages": messages,
        "tools": tool_schemas,
        "tool_choice": "auto",  # let the LLM decide when to call a tool
    }

    for attempt in (1, 2):
        response = httpx.post(
            API_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=60.0,
        )
        if response.status_code == 429 and attempt == 1:
            # Rate limited. Honor the retry-after header if present;
            # otherwise fall back to a 20s sleep.
            wait = _parse_retry_after(response) or 20.0
            print(f"[call_llm] rate-limited, sleeping {wait:.1f}s before retry", flush=True)
            time.sleep(wait + 1.0)  # +1s buffer to avoid landing right on the boundary
            continue
        if response.status_code >= 400:
            # Surface the API's actual error message (not just the status code).
            raise RuntimeError(
                f"API error {response.status_code}: {response.text}"
            )
        return response.json()["choices"][0]["message"]

    raise RuntimeError(f"API still rate-limited after retry: {response.text}")


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
    ) -> None:
        self.memory = memory
        # If memory is provided, paste the index into the system prompt so
        # the LLM sees what's already been remembered.
        full_prompt = system_prompt
        if memory is not None:
            full_prompt += "\n\n# Your memory\n\n" + memory.load_index()
        self.history: list[dict] = [{"role": "system", "content": full_prompt}]

    def reset(self) -> None:
        """Wipe history except for the system prompt."""
        self.history = self.history[:1]

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
        self.history.append({"role": "user", "content": user_message})
        if self.memory is not None:
            self.memory.log_turn("user", user_message)

        for _ in range(MAX_TURNS):
            assistant_msg = call_llm(self.history, tools.SCHEMAS)
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
                return reply

            # Otherwise: run each requested tool, append result, loop.
            for call in tool_calls:
                name = call["function"]["name"]
                args = json.loads(call["function"]["arguments"])
                self._log_tool_call(name, args)
                result = tools.execute(name, args)
                self.history.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": result,
                })

        return "(hit MAX_TURNS without a final answer)"

    @staticmethod
    def _log_tool_call(name: str, args: dict[str, Any]) -> None:
        """Print a short preview of each tool call as it happens, so the
        user can see what the agent is doing."""
        preview = ", ".join(f"{k}={repr(v)[:40]}" for k, v in args.items())
        print(f"  -> {name}({preview})")
