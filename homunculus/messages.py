"""Typed message models for the agent loop.

WHY THIS FILE EXISTS
====================

We used to pass plain `dict[str, Any]` around as the message format
everywhere:

    {"role": "user", "content": "hi", "ts": "...", "source": "web"}
    {"role": "assistant", "content": "hello", "tool_calls": [...]}
    {"role": "tool", "tool_call_id": "...", "content": "..."}

Plain dicts cost us:

  * Defensive validation on every read (load_session in memory.py
    iterates messages checking each role / required field).
  * Missing-field bugs at write time that only surface when the LLM
    rejects the next request as malformed.
  * Sprinkled `msg.get("role") == "tool"` checks instead of a single
    type predicate.

THE PATTERN (Letta / OpenClaw)
==============================

Letta uses Pydantic discriminated unions for messages — `UserMessage`,
`AssistantMessage`, `ToolCallMessage`, `ToolReturnMessage`,
`SystemMessage` — keyed off `role`. The OpenClaw house style is the
same: "discriminated unions / closed codes over freeform strings."

Discriminated unions give us:

  * One source of truth for what a message can look like.
  * Type-checking on construction — bad messages fail at the writer,
    not the reader.
  * Pattern-matching by message type instead of role-string checks.
  * Round-trip serialization for free via `.model_dump()` /
    `.model_validate()`.

WIRE COMPATIBILITY
==================

The LLM providers expect OpenAI-style dicts. We keep that contract:
the on-disk and on-wire format is unchanged — `Message.to_dict()`
emits exactly the same shape the providers and `_session.json`
already use. Only the in-memory representation gains types.

This file adds zero runtime cost — Pydantic v2 is C-backed and faster
than the `.get()` chains it replaces.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Tool-call schema (assistant → provider)
# ---------------------------------------------------------------------------
#
# This mirrors the OpenAI tool-call object shape. We don't invent our
# own — that would break compatibility with every provider we route to.


class ToolCallFunction(BaseModel):
    """Inner `function` block of an OpenAI tool_call."""

    model_config = ConfigDict(extra="allow")

    name: str
    # `arguments` is a JSON-encoded string on the wire — keep it as
    # string here so we don't have to re-encode every time we send.
    arguments: str = "{}"


class ToolCall(BaseModel):
    """One tool invocation the model asked us to run."""

    model_config = ConfigDict(extra="allow")

    id: str
    type: Literal["function"] = "function"
    function: ToolCallFunction


# ---------------------------------------------------------------------------
# Message variants — discriminated by `role`
# ---------------------------------------------------------------------------


class _BaseMessage(BaseModel):
    """Shared fields. Subclasses fix `role` to a Literal so the union
    can be discriminated."""

    model_config = ConfigDict(extra="allow")

    # Optional provenance metadata. Set by our transports / loop;
    # ignored by LLM providers (they receive only role/content/tool_*
    # via to_provider_dict).
    ts: str | None = None
    source: str | None = None


class SystemMessage(_BaseMessage):
    role: Literal["system"] = "system"
    content: str = ""


class UserMessage(_BaseMessage):
    role: Literal["user"] = "user"
    content: str = ""


class AssistantMessage(_BaseMessage):
    """An assistant turn. Two valid shapes:
      1. text reply           → `content` non-empty, `tool_calls` None
      2. tool-calling turn    → `tool_calls` non-empty, `content` may
                                 be empty (the model's "thinking")

    Both fields can coexist when a model produces text alongside tool
    calls (Anthropic does this; OpenAI usually doesn't). The validator
    layer treats messages with `tool_calls` as intermediate and
    messages with non-empty `content` AND no `tool_calls` as terminal.
    """

    role: Literal["assistant"] = "assistant"
    content: str | None = None
    tool_calls: list[ToolCall] | None = None


class ToolResultMessage(_BaseMessage):
    """Result the harness returns for one tool_call. `tool_call_id`
    links back to the assistant's request — providers reject orphans
    or duplicates so the loop must keep the pairing intact."""

    role: Literal["tool"] = "tool"
    tool_call_id: str
    content: str = ""


# Discriminated union — Pydantic picks the right subclass from `role`.
Message = Annotated[
    SystemMessage | UserMessage | AssistantMessage | ToolResultMessage,
    Field(discriminator="role"),
]


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------
#
# Two boundaries to cross:
#
#   * Disk / wire: plain dict with the OpenAI shape. Used by
#     _session.json, _chat_log.jsonl, transports, and provider HTTP
#     requests.
#   * In-memory typed: the Message classes above.
#
# Keep both directions cheap and total — never lose a field, never
# raise on a well-formed legacy dict.


def from_dict(data: dict[str, Any]) -> Message:
    """Parse a wire-format dict into a typed Message.

    Raises pydantic.ValidationError on malformed input. Callers that
    want best-effort loading (e.g. legacy session files with partial
    writes) should use `from_dict_lenient`.
    """
    # Pydantic discriminates on `role`; the rest is field-shape match.
    role = data.get("role")
    if role == "system":
        return SystemMessage.model_validate(data)
    if role == "user":
        return UserMessage.model_validate(data)
    if role == "assistant":
        return AssistantMessage.model_validate(data)
    if role == "tool":
        return ToolResultMessage.model_validate(data)
    raise ValueError(f"unknown message role: {role!r}")


def from_dict_lenient(data: dict[str, Any]) -> Message | None:
    """Best-effort parse. Returns None instead of raising — for legacy
    session files where partial writes or schema drift might have
    landed.
    """
    if not isinstance(data, dict):
        return None
    try:
        return from_dict(data)
    except Exception:
        return None


def to_dict(msg: Message) -> dict[str, Any]:
    """Serialise to the wire shape used by providers + on-disk files.

    Drops None values so the output dict matches what the agent loop
    constructed by hand — keeps `_session.json` byte-identical for
    unchanged messages and avoids confusing providers with null fields
    some of them reject (Gemini in particular).
    """
    return msg.model_dump(exclude_none=True, mode="json")


def to_provider_dict(msg: Message) -> dict[str, Any]:
    """Serialise the subset providers care about — drops our
    provenance metadata (`ts`, `source`) and anything else providers
    don't expect.

    Use this when building the HTTP body sent to the LLM; use
    `to_dict()` when persisting to disk or returning to the UI.
    """
    raw = to_dict(msg)
    raw.pop("ts", None)
    raw.pop("source", None)
    return raw


# ---------------------------------------------------------------------------
# Predicates — replace the `msg.get("role") == "..."` pattern
# ---------------------------------------------------------------------------


def is_system(msg: Message) -> bool:
    return isinstance(msg, SystemMessage)


def is_user(msg: Message) -> bool:
    return isinstance(msg, UserMessage)


def is_assistant(msg: Message) -> bool:
    return isinstance(msg, AssistantMessage)


def is_tool_result(msg: Message) -> bool:
    return isinstance(msg, ToolResultMessage)


def is_tool_call_turn(msg: Message) -> bool:
    """An assistant message that requested tool execution (no final
    text yet). Distinguishes intermediate planning from terminal
    replies — useful for `_visible_chat_history` which should skip
    intermediates."""
    return isinstance(msg, AssistantMessage) and bool(msg.tool_calls)


def is_terminal_assistant(msg: Message) -> bool:
    """An assistant message that ended a turn — has visible content
    AND no tool calls. This is what the UI renders as a chat bubble."""
    return (
        isinstance(msg, AssistantMessage)
        and not msg.tool_calls
        and bool((msg.content or "").strip())
    )


__all__ = [
    "AssistantMessage",
    "Message",
    "SystemMessage",
    "ToolCall",
    "ToolCallFunction",
    "ToolResultMessage",
    "UserMessage",
    "from_dict",
    "from_dict_lenient",
    "is_assistant",
    "is_system",
    "is_terminal_assistant",
    "is_tool_call_turn",
    "is_tool_result",
    "is_user",
    "to_dict",
    "to_provider_dict",
]
