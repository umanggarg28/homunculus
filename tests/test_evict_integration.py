"""End-to-end proof that eviction fires inside the live chat loop.

Stubs the LLM so we can drive the agent through two real turns and
inspect the persisted history. If this test starts failing, the
eviction codepath has regressed — the unit tests in
test_evict_prior_tool_results.py only cover the function in isolation.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import core


def _stub_llm_with_tool_call(history, tool_schemas, model=None):
    """First call: agent decides to read_file. Second call (after the
    tool result lands): agent gives a plain reply."""
    has_tool_result = any(m.get("role") == "tool" for m in history)
    if has_tool_result:
        return {"role": "assistant", "content": "Got it."}
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": "get_current_time", "arguments": "{}"},
            }
        ],
    }


def test_eviction_fires_between_turns(monkeypatch, tmp_path):
    monkeypatch.setenv("HOMUNCULUS_API_KEY", "stub")

    agent = core.Agent()
    # Patch the tool dispatcher so it returns a chunky string we can
    # later assert was replaced by a stub.
    big_payload = "X" * 1500

    import tools as tools_pkg

    monkeypatch.setattr(tools_pkg, "execute", lambda name, args: big_payload, raising=False)
    monkeypatch.setattr(core, "call_llm", _stub_llm_with_tool_call)
    # Skip the tool-arg validator that would reject our synthetic call.
    monkeypatch.setattr(core, "_validate_tool_args", lambda n, a: None)

    # Turn 1 — agent calls a tool, receives big_payload, replies.
    reply1 = agent.chat("first turn")
    assert reply1 == "Got it."
    # Confirm a tool message landed in history with the full payload.
    tool_msgs = [m for m in agent.history if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["content"] == big_payload

    # Turn 2 — eviction should fire at the start, stubbing the prior
    # tool result before the new user message is appended.
    agent.chat("second turn")

    tool_msgs = [m for m in agent.history if m.get("role") == "tool"]
    # We have at least one tool message from turn 1; that one MUST
    # now be a stub. Turn 2 also called a tool, so the most recent
    # tool message in this history will be a fresh full payload.
    turn1_msg = tool_msgs[0]
    assert "evicted from prior turn" in turn1_msg["content"], (
        f"turn-1 tool result was NOT evicted: {turn1_msg['content'][:80]!r}"
    )
    assert "1,500" in turn1_msg["content"], "stub should record original size"
    # tool_call_id must survive — required for OpenAI-style API pairing.
    assert turn1_msg.get("tool_call_id") == "call-1"


def test_eviction_emits_observability_event(monkeypatch):
    """The `tool_results_evicted` event is how the operator verifies
    eviction is running. If this regression-fires it means the
    user can no longer prove the feature is alive."""
    monkeypatch.setenv("HOMUNCULUS_API_KEY", "stub")

    agent = core.Agent()
    import tools as tools_pkg

    monkeypatch.setattr(tools_pkg, "execute", lambda n, a: "Y" * 500, raising=False)
    monkeypatch.setattr(core, "call_llm", _stub_llm_with_tool_call)
    monkeypatch.setattr(core, "_validate_tool_args", lambda n, a: None)

    events_captured: list[dict] = []
    import events

    real_emit = events.emit

    def capture_emit(event_name, **kwargs):
        events_captured.append({"event": event_name, **kwargs})
        return real_emit(event_name, **kwargs)

    monkeypatch.setattr(events, "emit", capture_emit)

    agent.chat("turn 1")
    agent.chat("turn 2")

    eviction_events = [e for e in events_captured if e["event"] == "tool_results_evicted"]
    assert eviction_events, (
        "expected at least one tool_results_evicted event; got events: "
        + ", ".join(e["event"] for e in events_captured)
    )
