"""Item 5 of the robustness plan (pragmatic slice) — pre_turn_hook.

A single turn-level hook installed in tools/ and called at the start of
each loop iteration in core._run_loop. The TaskGuard uses it to force a
final complete_task / record_failure call at iter MAX_TURNS-1 when any
due task is still unfinished — structural complement to the prompt
tightening.

Comparison to SOTA (Pi's agent-loop.ts uses prepareNextTurn,
transformContext, shouldStopAfterTurn, getSteeringMessages — four hooks).
We have one hook; it's the pragmatic slice that unblocks the forced-
completion feature today without a full LoopConfig refactor.
"""

import json
import sys
import types
from unittest.mock import patch

# tools.notify stub so heartbeat imports work
if "homunculus.tools.notify" not in sys.modules:
    _stub = types.ModuleType("homunculus.tools.notify")
    _stub._send_to_telegram = lambda *_a, **_kw: None
    sys.modules["homunculus.tools.notify"] = _stub

from homunculus import core  # noqa: E402
from homunculus import tools  # noqa: E402
from homunculus import events  # noqa: E402

# Defensive stubs for cross-test pollution
events.truncate_preview = lambda s, limit=200, **_kw: str(s)[:limit]
events.emit = lambda *_a, **_kw: None
events.full_text = lambda t: t

from homunculus.core import Agent  # noqa: E402
from homunculus.config import get_config  # noqa: E402

# MAX_TURNS moved into HomunculusConfig.loop.max_turns. Tests keep a
# local alias for readability.
MAX_TURNS = get_config().loop.max_turns
from homunculus.heartbeat import TaskGuard  # noqa: E402


def _tool_call_msg(name: str, args: dict, call_id: str = "c1") -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": call_id, "type": "function",
             "function": {"name": name, "arguments": json.dumps(args)}},
        ],
    }


def _final_reply(text: str) -> dict:
    return {"role": "assistant", "content": text}


def test_hook_fires_at_start_of_every_turn():
    """The hook is called once per iteration with the turn index."""
    agent = Agent(memory=None)
    seen_indices: list[int] = []
    # conftest stubs tools.set_pre_turn_hook as a no-op — set the attribute
    # directly so core._run_loop's `if tools._pre_turn_hook is not None`
    # branch fires.
    tools._pre_turn_hook = lambda i, _h: seen_indices.append(i) or None
    try:
        responses = iter([
            _tool_call_msg("read_file", {"path": "x.md"}),
            _final_reply("done"),
        ])
        with patch.object(core, "call_llm", lambda *_a, **_kw: next(responses)):
            with patch.object(tools, "execute", lambda *_a, **_kw: "ok", create=True):
                with patch.object(core, "_validate_tool_args", lambda *_a, **_kw: None):
                    list(agent._run_loop("trigger", streaming=False))
        # Hook saw turns 0 and 1
        assert seen_indices == [0, 1]
    finally:
        tools._pre_turn_hook = None


def test_hook_returning_message_injects_into_history():
    """When the hook returns a dict, it gets appended before the LLM call."""
    agent = Agent(memory=None)

    def hook(idx, _h):
        if idx == 1:
            return {"role": "user", "content": "harness: please wrap up"}
        return None
    tools._pre_turn_hook = hook  # bypass conftest no-op setter

    try:
        responses = iter([
            _tool_call_msg("read_file", {"path": "x.md"}),
            _final_reply("ok wrapping up"),
        ])
        with patch.object(core, "call_llm", lambda *_a, **_kw: next(responses)):
            with patch.object(tools, "execute", lambda *_a, **_kw: "ok", create=True):
                with patch.object(core, "_validate_tool_args", lambda *_a, **_kw: None):
                    list(agent._run_loop("trigger", streaming=False))
        # Find the injected message
        injected = [
            m for m in agent.history
            if m.get("role") == "user" and "wrap up" in (m.get("content") or "")
        ]
        assert len(injected) == 1
    finally:
        tools._pre_turn_hook = None


def test_task_guard_forces_completion_at_last_iter():
    """TaskGuard's on_pre_turn returns a forced completion message at iter
    MAX_TURNS-1 when expected_remaining() is non-empty.

    This is THE feature this hook exists to enable.
    """
    guard = TaskGuard({"task-a": []})
    # Earlier iterations → no injection
    assert guard.on_pre_turn(0, []) is None
    assert guard.on_pre_turn(MAX_TURNS - 3, []) is None
    # At the penultimate iteration → no injection (that's the budget nudge's job)
    assert guard.on_pre_turn(MAX_TURNS - 2, []) is None
    # At iter MAX_TURNS-1 with a task still unfinished → force a directive
    forced = guard.on_pre_turn(MAX_TURNS - 1, [])
    assert forced is not None
    content = forced["content"]
    assert "task-a" in content
    assert "complete_task" in content
    assert "record_failure" in content
    assert "last iteration" in content.lower() or "harness directive" in content.lower()


def test_task_guard_skips_force_if_task_already_completed():
    """If the agent already called complete_task, the forced message
    shouldn't fire — nothing to force."""
    guard = TaskGuard({"task-a": []})
    guard.on_tool_call("complete_task", {"task_id": "task-a", "result": "done"})
    assert guard.on_pre_turn(MAX_TURNS - 1, []) is None
