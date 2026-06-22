"""Pydantic message models + dict round-trip.

These pin the contract that any wire-format dict the existing code
produces round-trips through the typed models without losing fields or
mutating their shape. The agent loop continues to send dicts to
providers and persist dicts to disk — the typed layer sits in between
for in-memory work.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

import homunculus.messages as msgs


# ---------------------------------------------------------------------------
# Construction + basic shape
# ---------------------------------------------------------------------------


def test_system_message_default_role():
    m = msgs.SystemMessage(content="you are a helpful agent")
    assert m.role == "system"
    assert m.content == "you are a helpful agent"
    assert m.ts is None
    assert m.source is None


def test_user_message_with_metadata():
    m = msgs.UserMessage(
        content="hello",
        ts="2026-06-07T13:00:00+00:00",
        source="web",
    )
    assert m.role == "user"
    assert m.ts == "2026-06-07T13:00:00+00:00"
    assert m.source == "web"


def test_assistant_text_reply():
    m = msgs.AssistantMessage(content="hi there", source="telegram")
    assert m.role == "assistant"
    assert m.content == "hi there"
    assert m.tool_calls is None


def test_assistant_tool_call_turn():
    call = msgs.ToolCall(
        id="call-1",
        function=msgs.ToolCallFunction(
            name="read_file",
            arguments='{"path":"memory/note.md"}',
        ),
    )
    m = msgs.AssistantMessage(content=None, tool_calls=[call])
    assert m.tool_calls is not None
    assert m.tool_calls[0].function.name == "read_file"


def test_tool_result_requires_id():
    """The provider rejects tool messages missing tool_call_id, so the
    model must enforce it."""
    with pytest.raises(ValidationError):
        msgs.ToolResultMessage(content="result")


def test_tool_result_with_id():
    m = msgs.ToolResultMessage(tool_call_id="call-1", content="ok")
    assert m.tool_call_id == "call-1"


# ---------------------------------------------------------------------------
# Round trip with the actual shapes used in core.py
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", [
    {"role": "system", "content": "you are an agent"},
    {"role": "user", "content": "hi"},
    {"role": "user", "content": "hi", "ts": "2026-06-07T13:00:00+00:00", "source": "web"},
    {"role": "assistant", "content": "hello"},
    {"role": "assistant", "content": "", "tool_calls": [
        {"id": "c1", "type": "function",
         "function": {"name": "read_file", "arguments": '{"path":"x"}'}},
    ]},
    {"role": "assistant", "content": "summary", "tool_calls": [
        {"id": "c2", "type": "function",
         "function": {"name": "notify", "arguments": '{"text":"hi"}'}},
    ]},
    {"role": "tool", "tool_call_id": "c1", "content": "file contents here"},
])
def test_round_trip_preserves_wire_shape(raw):
    """Every shape the existing code produces must survive a
    dict → typed → dict round trip with the same JSON bytes."""
    parsed = msgs.from_dict(raw)
    rendered = msgs.to_dict(parsed)
    # Exclude_none normalisation: any None we added should be absent.
    expected = {k: v for k, v in raw.items() if v is not None}
    assert rendered == expected, (
        f"round-trip changed shape:\n  in:  {expected}\n  out: {rendered}"
    )


def test_from_dict_lenient_returns_none_on_garbage():
    for garbage in (None, "string", 42, {}, {"role": "alien"}):
        assert msgs.from_dict_lenient(garbage) is None


def test_from_dict_raises_on_unknown_role():
    with pytest.raises(ValueError):
        msgs.from_dict({"role": "alien", "content": "?"})


# ---------------------------------------------------------------------------
# Provider-bound dict drops our provenance metadata
# ---------------------------------------------------------------------------


def test_to_provider_dict_strips_ts_and_source():
    m = msgs.UserMessage(content="hi", ts="2026-06-07T00:00:00+00:00", source="web")
    out = msgs.to_provider_dict(m)
    assert out == {"role": "user", "content": "hi"}


def test_to_provider_dict_keeps_tool_calls():
    m = msgs.AssistantMessage(
        content=None,
        tool_calls=[
            msgs.ToolCall(
                id="c1",
                function=msgs.ToolCallFunction(name="x", arguments="{}"),
            )
        ],
        source="web",
        ts="2026-06-07T00:00:00+00:00",
    )
    out = msgs.to_provider_dict(m)
    assert "tool_calls" in out
    assert "source" not in out
    assert "ts" not in out


# ---------------------------------------------------------------------------
# Predicates
# ---------------------------------------------------------------------------


def test_predicates_select_correct_shapes():
    system = msgs.SystemMessage(content="sys")
    user = msgs.UserMessage(content="u")
    text_reply = msgs.AssistantMessage(content="reply")
    tool_turn = msgs.AssistantMessage(
        content=None,
        tool_calls=[msgs.ToolCall(
            id="c", function=msgs.ToolCallFunction(name="x", arguments="{}"),
        )],
    )
    tool_result = msgs.ToolResultMessage(tool_call_id="c", content="r")

    assert msgs.is_system(system)
    assert msgs.is_user(user)
    assert msgs.is_assistant(text_reply)
    assert msgs.is_assistant(tool_turn)
    assert msgs.is_tool_result(tool_result)

    assert msgs.is_terminal_assistant(text_reply)
    assert not msgs.is_terminal_assistant(tool_turn)
    assert not msgs.is_terminal_assistant(user)

    assert msgs.is_tool_call_turn(tool_turn)
    assert not msgs.is_tool_call_turn(text_reply)


def test_terminal_assistant_requires_visible_content():
    """An assistant message with only whitespace content shouldn't be
    treated as terminal — the UI would render an empty bubble."""
    blank = msgs.AssistantMessage(content="   \n  ")
    assert not msgs.is_terminal_assistant(blank)


# ---------------------------------------------------------------------------
# Edge cases that bit production
# ---------------------------------------------------------------------------


def test_assistant_with_both_content_and_tool_calls():
    """Anthropic models sometimes emit a `content` summary alongside
    tool_calls — the loop must treat this as a tool-call turn (the
    tool result comes next), not as a final reply."""
    m = msgs.AssistantMessage(
        content="Let me check the file first.",
        tool_calls=[msgs.ToolCall(
            id="c1",
            function=msgs.ToolCallFunction(name="read_file", arguments="{}"),
        )],
    )
    assert msgs.is_tool_call_turn(m)
    assert not msgs.is_terminal_assistant(m)


def test_legacy_session_message_loads_lenient():
    """_session.json from before this refactor doesn't carry `ts` or
    `source`. The lenient loader must still accept it."""
    parsed = msgs.from_dict_lenient({"role": "user", "content": "old message"})
    assert isinstance(parsed, msgs.UserMessage)
    assert parsed.ts is None
    assert parsed.source is None


def test_extra_fields_preserved_via_model_config():
    """We mark `extra='allow'` so unknown provider-specific fields don't
    get dropped silently — if Gemini ever adds a `reasoning_content`
    sidecar we shouldn't lose it on round trip."""
    parsed = msgs.from_dict({
        "role": "assistant",
        "content": "ok",
        "reasoning": "unused but mustn't vanish",
    })
    rendered = msgs.to_dict(parsed)
    assert rendered.get("reasoning") == "unused but mustn't vanish"


def test_json_round_trip_via_json_module():
    """Belt and braces — serialise to JSON, parse back, type-check."""
    m = msgs.AssistantMessage(
        content="done",
        tool_calls=None,
        ts="2026-06-07T13:00:00+00:00",
        source="web",
    )
    blob = json.dumps(msgs.to_dict(m), sort_keys=True)
    raw = json.loads(blob)
    rebuilt = msgs.from_dict(raw)
    assert isinstance(rebuilt, msgs.AssistantMessage)
    assert rebuilt.content == "done"
    assert rebuilt.ts == "2026-06-07T13:00:00+00:00"
