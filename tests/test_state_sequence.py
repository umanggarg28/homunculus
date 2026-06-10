"""Pi-style per-turn state machine via `state_sequence` on the agent.

When the caller supplies `state_sequence=[{tool, args?}, ...]`, each
loop turn is governed by the next entry until the list is exhausted:

  - {"tool": "X", "args": {...}}  → deterministic. The LLM is skipped;
    a synthetic assistant_msg is fabricated locally and flows through
    the normal dispatch path. Used for known-args context loads
    (read_file of a fixed path, recall of a known key) so the model
    can't decide to skip them.

  - {"tool": "X"}                 → model-driven. tool_choice is pinned
    to `{"type":"function", "function":{"name":"X"}}` for this turn
    only; the model fills the args. The existing required-tool-call
    detector handles the no-tool-call retry.

After the list is exhausted the loop reverts to source-default
tool_choice (Pi pattern — pin only the bottleneck steps; let the model
handle the tail).
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

import core
import tools as tools_module


# ---- shared fixtures -------------------------------------------------


def _schema(name: str) -> dict:
    """Minimal schema that satisfies _validate_tool_args: no required
    fields, so any args (or none) pass."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": "test stub",
            "parameters": {"type": "object", "properties": {}},
        },
    }


@pytest.fixture
def stubbed_tools(monkeypatch):
    """Install schemas + an execute() stub on the conftest tools module
    so dispatch doesn't get blocked by 'tool does not exist' validation.
    Returns the dispatched-call ledger so tests can assert against it."""
    schemas = [
        _schema(n) for n in ("read_file", "web_post", "notify", "complete_task")
    ]
    monkeypatch.setattr(tools_module, "SCHEMAS", schemas, raising=False)
    dispatched: list[tuple[str, dict]] = []

    def fake_execute(name: str, args: dict) -> str:
        dispatched.append((name, args))
        return "OK"

    monkeypatch.setattr(tools_module, "execute", fake_execute, raising=False)
    return dispatched


def _exit_via_complete_task() -> dict:
    """Return value for fake call_llm — emits a complete_task so the
    enclosing loop can keep iterating without exploding. (The loop
    only naturally exits on tool_choice=auto with no tool_calls; for
    these tests we just let it run to max_turns and assert behavior.)"""
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": "ct",
            "type": "function",
            "function": {
                "name": "complete_task",
                "arguments": '{"task_id":"x","result":"ok"}',
            },
        }],
    }


# ---- deterministic state (args supplied) ------------------------------


def test_static_args_state_dispatches_tool_locally(stubbed_tools):
    """An entry with `args` set must invoke dispatch directly without
    asking the LLM. This is the load-context primitive: known-args
    read_file/recall that the harness wants to happen before the model
    gets to choose."""
    agent = core.Agent(memory=None)

    with patch.object(core, "call_llm", return_value=_exit_via_complete_task()):
        list(agent._run_loop(
            "tick", streaming=False, source="heartbeat",
            state_sequence=[{"tool": "read_file", "args": {"path": "memory/x.md"}}],
        ))

    # read_file dispatched once with the literal args from the state.
    read_calls = [c for c in stubbed_tools if c[0] == "read_file"]
    assert read_calls == [("read_file", {"path": "memory/x.md"})], (
        f"deterministic state must dispatch its tool with state args, got: {read_calls}"
    )


def test_static_args_state_does_not_invoke_llm(stubbed_tools):
    """Deterministic states must skip the LLM entirely. Validates the
    LLM-skip path: with N deterministic states + 1 free-form tail
    turn, call_llm fires exactly once."""
    agent = core.Agent(memory=None)
    llm_call_count = {"n": 0}

    def fake(messages, tool_schemas, model=None, tool_choice="auto",
             reasoning_effort="low", provider_constraints=None):
        llm_call_count["n"] += 1
        return _exit_via_complete_task()

    states = [
        {"tool": "read_file", "args": {"path": "a.md"}},
        {"tool": "read_file", "args": {"path": "b.md"}},
        {"tool": "read_file", "args": {"path": "c.md"}},
    ]
    with patch.object(core, "call_llm", side_effect=fake):
        list(agent._run_loop(
            "tick", streaming=False, source="heartbeat",
            state_sequence=states,
        ))

    # 3 deterministic states = 0 LLM calls; the free-form tail fires it.
    # The loop will keep running until max_turns since complete_task
    # doesn't auto-exit in this stubbed environment, but the FIRST 3
    # iterations must not have hit call_llm.
    # We expect call_llm hits = (total_iters - 3 deterministic states).
    # The simpler invariant: dispatched ledger MUST start with read_file ×3.
    first_three = [c[0] for c in stubbed_tools[:3]]
    assert first_three == ["read_file", "read_file", "read_file"], (
        f"first three dispatches must be the deterministic states, got: {first_three}"
    )
    # And the args must come from the state list, not the model.
    assert stubbed_tools[0][1] == {"path": "a.md"}
    assert stubbed_tools[1][1] == {"path": "b.md"}
    assert stubbed_tools[2][1] == {"path": "c.md"}


# ---- model-driven state (no args) -------------------------------------


def test_tool_only_state_pins_tool_choice_to_dict_form(stubbed_tools):
    """A state with `tool` but no `args` must override tool_choice for
    that turn to the OpenAI dict shape, leaving args to the model."""
    agent = core.Agent(memory=None)
    captured: list = []

    def fake(messages, tool_schemas, model=None, tool_choice="auto",
             reasoning_effort="low", provider_constraints=None):
        captured.append(tool_choice)
        return _exit_via_complete_task()

    with patch.object(core, "call_llm", side_effect=fake):
        list(agent._run_loop(
            "tick", streaming=False, source="heartbeat",
            state_sequence=[{"tool": "web_post"}],
        ))

    assert captured, "expected an LLM call"
    assert captured[0] == {"type": "function", "function": {"name": "web_post"}}, (
        f"first turn must use dict tool_choice for web_post, got: {captured[0]!r}"
    )


def test_post_state_turns_revert_to_source_default(stubbed_tools):
    """After the state list is exhausted, tool_choice goes back to the
    source-default (here: 'required' for heartbeat). Pi pattern: pin
    bottleneck steps only, free the tail."""
    agent = core.Agent(memory=None)
    captured: list = []

    def fake(messages, tool_schemas, model=None, tool_choice="auto",
             reasoning_effort="low", provider_constraints=None):
        captured.append(tool_choice)
        return _exit_via_complete_task()

    with patch.object(core, "call_llm", side_effect=fake):
        list(agent._run_loop(
            "tick", streaming=False, source="heartbeat",
            state_sequence=[{"tool": "web_post"}],
        ))

    assert len(captured) >= 2, (
        f"expected pinned-turn + at least one free-form turn, got {captured}"
    )
    # First call uses dict (pinned to web_post).
    assert isinstance(captured[0], dict)
    # Subsequent calls revert to source-default "required" (heartbeat).
    for tc in captured[1:]:
        assert tc == "required", (
            f"post-state turn must revert to source default, got: {tc!r}"
        )


# ---- mixed sequence ---------------------------------------------------


def test_mixed_static_and_model_driven_states_run_in_order(stubbed_tools):
    """Realistic skill shape: deterministic context load, then forced
    tools where the model fills args."""
    agent = core.Agent(memory=None)
    seen_tool_choices: list = []

    def fake(messages, tool_schemas, model=None, tool_choice="auto",
             reasoning_effort="low", provider_constraints=None):
        seen_tool_choices.append(tool_choice)
        return _exit_via_complete_task()

    states = [
        {"tool": "read_file", "args": {"path": "a.md"}},   # deterministic
        {"tool": "web_post"},                              # forced
        {"tool": "notify"},                                # forced
    ]
    with patch.object(core, "call_llm", side_effect=fake):
        list(agent._run_loop(
            "tick", streaming=False, source="heartbeat",
            state_sequence=states,
        ))

    # Deterministic read_file must dispatch first.
    assert stubbed_tools[0] == ("read_file", {"path": "a.md"})
    # Then two LLM calls with dict tool_choice (web_post then notify).
    assert len(seen_tool_choices) >= 2
    assert seen_tool_choices[0] == {"type": "function", "function": {"name": "web_post"}}
    assert seen_tool_choices[1] == {"type": "function", "function": {"name": "notify"}}


# ---- signature / default ----------------------------------------------


def test_state_sequence_default_none_preserves_existing_behavior():
    """A missing state_sequence must keep the legacy single-knob
    per-source dispatch intact. Regression net for every caller that
    hasn't been migrated."""
    import inspect
    sig = inspect.signature(core.Agent._run_loop)
    assert "state_sequence" in sig.parameters
    assert sig.parameters["state_sequence"].default is None
    sig_chat = inspect.signature(core.Agent.chat)
    assert sig_chat.parameters["state_sequence"].default is None
    sig_stream = inspect.signature(core.Agent.chat_stream)
    assert sig_stream.parameters["state_sequence"].default is None


def test_state_missing_tool_field_raises(stubbed_tools):
    """Malformed state — no `tool` — must fail loudly, not silently skip."""
    agent = core.Agent(memory=None)
    with patch.object(core, "call_llm", return_value=_exit_via_complete_task()):
        with pytest.raises(RuntimeError, match="tool"):
            list(agent._run_loop(
                "tick", streaming=False, source="heartbeat",
                state_sequence=[{"args": {"x": 1}}],
            ))


# ---- synthesized message format ---------------------------------------


def test_synthesized_assistant_msg_arguments_are_json_string(stubbed_tools):
    """The synthesized tool_call's `arguments` field must be a JSON
    string (not a dict) to match the OpenAI message shape — otherwise
    downstream replay to the API would error."""
    agent = core.Agent(memory=None)
    captured_history: list = []

    def fake(messages, tool_schemas, model=None, tool_choice="auto",
             reasoning_effort="low", provider_constraints=None):
        captured_history.append([dict(m) for m in messages])
        return _exit_via_complete_task()

    states = [{"tool": "read_file", "args": {"path": "a.md", "nested": {"k": 1}}}]
    with patch.object(core, "call_llm", side_effect=fake):
        list(agent._run_loop(
            "tick", streaming=False, source="heartbeat",
            state_sequence=states,
        ))

    # Find the synthesized assistant message in the history shown to the
    # LLM on the post-state free-form turn.
    assert captured_history, "expected at least one LLM call"
    found = False
    for msg in captured_history[0]:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for call in msg["tool_calls"]:
                if call["function"]["name"] == "read_file":
                    args_raw = call["function"]["arguments"]
                    assert isinstance(args_raw, str), (
                        f"arguments must be JSON string, got {type(args_raw)}"
                    )
                    parsed = json.loads(args_raw)
                    assert parsed == {"path": "a.md", "nested": {"k": 1}}
                    found = True
    assert found, "expected synthesized read_file call in replayed history"


# ---- detector retry rolls state_idx back so retry re-runs same state ----


def test_required_tool_violation_retry_re_runs_same_state(stubbed_tools):
    """Bug 2026-06-10: state 2 forced web_post, model returned prose,
    detector retried — but state_idx had already advanced, so the
    retry fired state 3 (next forced tool) instead of re-running
    state 2's forced tool. Broke dependency chains in skill playbooks.
    Fix: on detector retry, roll state_idx back by 1."""
    agent = core.Agent(memory=None)
    seen_tool_choices: list = []
    call_count = {"n": 0}

    def fake(messages, tool_schemas, model=None, tool_choice="auto",
             reasoning_effort="low", provider_constraints=None):
        seen_tool_choices.append(tool_choice)
        call_count["n"] += 1
        # Turn 1: pretend the model returned prose (no tool call).
        # The detector should retry the SAME state.
        if call_count["n"] == 1:
            return {"role": "assistant", "content": "let me think...", "tool_calls": None}
        # Turn 2+: comply by calling complete_task to exit cleanly.
        return _exit_via_complete_task()

    states = [
        {"tool": "web_post"},  # state we want to retry
        {"tool": "notify"},    # state that MUST NOT fire as the retry
    ]
    with patch.object(core, "call_llm", side_effect=fake):
        list(agent._run_loop(
            "tick", streaming=False, source="heartbeat",
            state_sequence=states,
        ))

    # First call: forced web_post (the state we want).
    assert seen_tool_choices[0] == {"type": "function", "function": {"name": "web_post"}}
    # Second call (the retry): MUST also be forced web_post (re-running
    # state 0), NOT forced notify (state 1). This is the fix.
    assert seen_tool_choices[1] == {"type": "function", "function": {"name": "web_post"}}, (
        f"detector retry must re-run the same state, not advance. "
        f"got: {seen_tool_choices[1]!r}"
    )
