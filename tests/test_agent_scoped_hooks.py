"""Run-scoped guard hooks: a guard supervises exactly the Agent it rides.

The regression behind this file: hooks used to be process-global
(tools.set_pre_execute_hook), and the web run-now endpoint installed a
TaskGuard through them in the same uvicorn process that serves chat. A chat
turn during a manual task run had its tool calls checked against the task's
success criteria (its notify() could be BLOCKED) and its tool results leaked
into the task's link-grounding blob. Instance scoping — hooks passed to the
Agent constructor — makes the cross-talk structurally impossible: there is
no shared mutable slot for two runs to fight over.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from homunculus import core, tools
from homunculus.core import Agent


def _tool_call_msg(name: str, args: dict | None = None) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": f"call_{name}",
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(args or {"path": "x.md"})},
        }],
    }


def _final(text: str = "done") -> dict:
    return {"role": "assistant", "content": text}


def _run(agent: Agent, responses: list[dict], executed: list[str]) -> str:
    """Drive one _run_loop turn against a scripted LLM and a recording
    tools.execute. Schema validation is bypassed — the stub tools module
    has no schemas, and validation order (before the hook) is not what
    these tests pin."""
    it = iter(responses)
    with patch.object(core, "call_llm", lambda *_a, **_kw: next(it)):
        with patch.object(core, "_validate_tool_args", lambda _n, _a: None):
            with patch.object(
                tools, "execute",
                lambda name, args: executed.append(name) or "tool ran ok",
                create=True,
            ):
                return "".join(agent._run_loop("go", streaming=False))


def test_agent_pre_execute_hook_blocks_the_call():
    blocked_calls: list[str] = []

    def guard(name: str, _args: dict) -> str | None:
        blocked_calls.append(name)
        return "BLOCKED: criteria not satisfied" if name == "read_file" else None

    agent = Agent(memory=None, pre_execute_hook=guard)
    executed: list[str] = []
    _run(agent, [_tool_call_msg("read_file"), _final()], executed)

    assert blocked_calls == ["read_file"]
    assert executed == [], "a blocked call must never reach tools.execute"
    tool_results = [m for m in agent.history if m.get("role") == "tool"]
    assert tool_results and "BLOCKED: criteria not satisfied" in tool_results[0]["content"]


def test_agent_post_execute_hook_observes_real_executions_only():
    observed: list[tuple[str, str]] = []

    def block_notify(name: str, _args: dict) -> str | None:
        return "BLOCKED" if name == "notify" else None

    agent = Agent(
        memory=None,
        pre_execute_hook=block_notify,
        post_execute_hook=lambda name, result: observed.append((name, result)),
    )
    executed: list[str] = []
    _run(
        agent,
        [_tool_call_msg("read_file"), _tool_call_msg("notify", {"text": "hi"}), _final()],
        executed,
    )

    assert executed == ["read_file"]
    assert observed == [("read_file", "tool ran ok")], (
        "the guard must ground only against output that a tool actually produced"
    )


def test_hooks_do_not_leak_between_agents():
    """The cross-talk regression: while a guarded run is active, an
    UNGUARDED agent in the same process must be completely unaffected."""
    def deny_everything(_name: str, _args: dict) -> str | None:
        return "BLOCKED: task criteria"

    guarded = Agent(memory=None, pre_execute_hook=deny_everything)
    chat = Agent(memory=None)  # a concurrent chat session — no guard

    executed: list[str] = []
    _run(chat, [_tool_call_msg("read_file"), _final("chat fine")], executed)

    assert executed == ["read_file"], (
        "the unguarded agent's tool call was intercepted by another run's guard"
    )
    assert guarded._pre_execute_hook is not None  # the guard stayed where it belongs


def test_agent_pre_turn_hook_injects_before_llm_call():
    def inject_at_first_turn(turn_idx: int, _history: list) -> dict | None:
        if turn_idx == 0:
            return {"role": "user", "content": "HARNESS DIRECTIVE: wrap up"}
        return None

    agent = Agent(memory=None, pre_turn_hook=inject_at_first_turn)
    executed: list[str] = []
    _run(agent, [_final()], executed)

    injected = [
        m for m in agent.history
        if m.get("role") == "user" and "HARNESS DIRECTIVE" in (m.get("content") or "")
    ]
    assert len(injected) == 1


def test_module_global_pre_turn_hook_still_works_as_fallback():
    """Back-compat: tests (and only tests) may still install the module-global
    hook; an Agent without its own hook falls back to it."""
    seen: list[int] = []
    tools._pre_turn_hook = lambda i, _h: seen.append(i) or None
    try:
        agent = Agent(memory=None)
        executed: list[str] = []
        _run(agent, [_final()], executed)
        assert seen == [0]
    finally:
        tools._pre_turn_hook = None


def test_agent_hook_takes_precedence_over_module_global():
    global_seen: list[int] = []
    own_seen: list[int] = []
    tools._pre_turn_hook = lambda i, _h: global_seen.append(i) or None
    try:
        agent = Agent(memory=None, pre_turn_hook=lambda i, _h: own_seen.append(i) or None)
        executed: list[str] = []
        _run(agent, [_final()], executed)
        assert own_seen == [0]
        assert global_seen == [], "an Agent with its own hook must not also run the global one"
    finally:
        tools._pre_turn_hook = None
