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


def _stub_llm_with_tool_call(history, tool_schemas, model=None, tool_choice="auto"):
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
                "function": {"name": "search_files", "arguments": "{}"},
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
    # search_files is an untrusted-content tool so its result is wrapped
    # in a delimited envelope; the original payload must still be inside.
    tool_msgs = [m for m in agent.history if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert big_payload in tool_msgs[0]["content"]

    # Turn 2 — eviction should fire at the start, stubbing the prior
    # tool result before the new user message is appended.
    agent.chat("second turn")

    tool_msgs = [m for m in agent.history if m.get("role") == "tool"]
    # We have at least one tool message from turn 1; that one MUST
    # now be a stub. Turn 2 also called a tool, so the most recent
    # tool message in this history will be a fresh full payload.
    turn1_msg = tool_msgs[0]
    assert "tool result evicted" in turn1_msg["content"], (
        f"turn-1 tool result was NOT evicted: {turn1_msg['content'][:80]!r}"
    )
    # Stub should record the evicted size. Untrusted-content tools are
    # wrapped in a delimited envelope before eviction, so the recorded
    # size is the wrapped size (≈original + a small constant) — assert
    # on order of magnitude rather than the exact byte count.
    assert "1," in turn1_msg["content"], (
        f"stub should mention the size of the evicted payload: {turn1_msg['content']!r}"
    )
    # tool_call_id must survive — required for OpenAI-style API pairing.
    assert turn1_msg.get("tool_call_id") == "call-1"


def test_mid_loop_eviction_keeps_per_call_input_bounded(monkeypatch):
    """The user-visible symptom we're fixing: per-call input shouldn't
    grow linearly across iterations within a single agent loop. With
    keep_recent=2, after iter 5 we should still have only 2 full tool
    payloads in history — the rest must be stubs.
    """
    monkeypatch.setenv("HOMUNCULUS_API_KEY", "stub")

    agent = core.Agent()
    big = "Z" * 2000
    import tools as tools_pkg
    monkeypatch.setattr(tools_pkg, "execute", lambda n, a: big, raising=False)
    monkeypatch.setattr(core, "_validate_tool_args", lambda n, a: None)

    # LLM stub: keep emitting tool_calls for 5 iterations, then a plain
    # reply. Models a tool-heavy task like the leetcode delivery.
    iter_counter = {"n": 0}

    def stub(history, tool_schemas, model=None, tool_choice="auto"):
        iter_counter["n"] += 1
        if iter_counter["n"] >= 6:
            return {"role": "assistant", "content": "done."}
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": f"call-{iter_counter['n']}",
                "type": "function",
                "function": {"name": "search_files", "arguments": "{}"},
            }],
        }

    monkeypatch.setattr(core, "call_llm", stub)
    agent.chat("kick off")

    # Count full vs evicted tool messages after the loop.
    tool_msgs = [m for m in agent.history if m.get("role") == "tool"]
    full = [m for m in tool_msgs if "tool result evicted" not in m["content"]]
    stubs = [m for m in tool_msgs if "tool result evicted" in m["content"]]
    # 5 tool calls fired → 5 tool messages; with keep_recent=2 only the
    # last 2 should be full payload, the other 3 must be stubs.
    assert len(tool_msgs) == 5, [m["content"][:40] for m in tool_msgs]
    assert len(full) == 2, f"expected 2 full, got {len(full)}"
    assert len(stubs) == 3, f"expected 3 stubs, got {len(stubs)}"


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
