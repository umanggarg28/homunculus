"""Item 3 of the robustness plan — per-turn cache for read-only tools.

When the LLM re-calls a read-only tool with identical arguments within a
single turn, the harness should return the cached result with a hint
instead of re-executing. This both saves cycles and prevents the worse
stuck-loop path which fires only at count 3 and tells the model to "pivot"
when often it just needs the same data again to proceed.

Today's LeetCode tick had 3× read_file(tracker) before pivoting to web
search. With caching, the 2nd and 3rd calls return instantly with a hint
that the agent already has the data — letting it move on faster.
"""

import json
from unittest.mock import patch

import core
import tools
from core import Agent, READ_ONLY_CACHEABLE_TOOLS

# Defensive stubs for cross-test pollution (see test_max_turns_nudge.py).
import events  # noqa: E402
events.truncate_preview = lambda s, limit=200, **_kw: str(s)[:limit]
events.emit = lambda *_a, **_kw: None
events.full_text = lambda t: t


def _tool_call_msg(name: str, args: dict, call_id: str = "c1") -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
        ],
    }


def _final_reply(text: str) -> dict:
    return {"role": "assistant", "content": text}


def test_cacheable_tool_is_cached_on_second_call():
    """Second read_file with same path returns the cached body with a hint,
    and the underlying tool is called exactly once."""
    agent = Agent(memory=None)
    execute_calls: list[tuple[str, dict]] = []

    def fake_execute(name, args):
        execute_calls.append((name, args))
        return "FILE_CONTENT_FRESH_FROM_DISK"

    responses = iter([
        _tool_call_msg("read_file", {"path": "x.md"}, "c1"),
        _tool_call_msg("read_file", {"path": "x.md"}, "c2"),
        _final_reply("done"),
    ])

    with patch.object(core, "call_llm", lambda *_a, **_kw: next(responses)):
        with patch.object(tools, "execute", fake_execute, create=True):
            # conftest stubs tools.SCHEMAS to []; the validator then rejects
            # every tool name as "does not exist" and execute is never called.
            # Bypass validation here so the loop actually dispatches.
            with patch.object(core, "_validate_tool_args", lambda *_a, **_kw: None):
                list(agent._run_loop("trigger", streaming=False))

    # Tool actually executed only once — second call was a cache hit
    assert len(execute_calls) == 1
    assert execute_calls[0] == ("read_file", {"path": "x.md"})

    # History has two tool results. The second one should be the cached
    # variant with the hint, NOT a stuck-loop error.
    tool_results = [m for m in agent.history if m.get("role") == "tool"]
    assert len(tool_results) == 2
    second = tool_results[1]["content"]
    assert "harness-cached" in second
    assert "FILE_CONTENT_FRESH_FROM_DISK" in second
    assert "STUCK_LOOP" not in second


def test_non_cacheable_tool_is_not_cached():
    """notify() must NEVER be cached — sending the same notification twice
    would actually deliver two messages."""
    assert "notify" not in READ_ONLY_CACHEABLE_TOOLS
    assert "complete_task" not in READ_ONLY_CACHEABLE_TOOLS
    assert "write_file" not in READ_ONLY_CACHEABLE_TOOLS

    agent = Agent(memory=None)
    execute_calls: list[tuple[str, dict]] = []

    def fake_execute(name, args):
        execute_calls.append((name, args))
        return "delivered"

    responses = iter([
        _tool_call_msg("notify", {"text": "hi"}, "c1"),
        _tool_call_msg("notify", {"text": "hi"}, "c2"),
        _final_reply("done"),
    ])

    with patch.object(core, "call_llm", lambda *_a, **_kw: next(responses)):
        with patch.object(tools, "execute", fake_execute, create=True):
            # conftest stubs tools.SCHEMAS to []; the validator then rejects
            # every tool name as "does not exist" and execute is never called.
            # Bypass validation here so the loop actually dispatches.
            with patch.object(core, "_validate_tool_args", lambda *_a, **_kw: None):
                list(agent._run_loop("trigger", streaming=False))

    # notify ran TWICE — caching it would have silently dropped the second
    # notification, which would be a real correctness bug.
    assert len(execute_calls) == 2


def test_errors_are_not_cached():
    """A tool that errors on the first call must NOT have the error string
    cached — the agent might retry with different conditions and want a
    real result."""
    agent = Agent(memory=None)
    call_count = {"n": 0}

    def fake_execute(name, args):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return "ERROR: transient failure"
        return "FRESH_RETRY_BODY"

    responses = iter([
        _tool_call_msg("read_file", {"path": "y.md"}, "c1"),
        _tool_call_msg("read_file", {"path": "y.md"}, "c2"),
        _final_reply("done"),
    ])

    with patch.object(core, "call_llm", lambda *_a, **_kw: next(responses)):
        with patch.object(tools, "execute", fake_execute, create=True):
            # conftest stubs tools.SCHEMAS to []; the validator then rejects
            # every tool name as "does not exist" and execute is never called.
            # Bypass validation here so the loop actually dispatches.
            with patch.object(core, "_validate_tool_args", lambda *_a, **_kw: None):
                list(agent._run_loop("trigger", streaming=False))

    # The error was returned the first time; the retry must hit the tool
    # again (not get cached error).
    assert call_count["n"] == 2
    tool_results = [m for m in agent.history if m.get("role") == "tool"]
    assert "ERROR" in tool_results[0]["content"]
    assert "FRESH_RETRY_BODY" in tool_results[1]["content"]


def test_today_leetcode_shape_no_longer_thrashes():
    """Replay: agent calls read_file('tracker') 3× in a row.
    With caching, only the first call hits disk; the 2nd and 3rd return
    cached + hint instantly. No STUCK_LOOP error gets fired because the
    cache hit takes priority over the count-3 detector."""
    agent = Agent(memory=None)
    execute_calls = 0

    def fake_execute(name, args):
        nonlocal execute_calls
        execute_calls += 1
        return "# tracker content\n['Two Sum', 'Merge Sorted Array']"

    responses = iter([
        _tool_call_msg("read_file", {"path": "tracker.md"}, "c1"),
        _tool_call_msg("read_file", {"path": "tracker.md"}, "c2"),
        _tool_call_msg("read_file", {"path": "tracker.md"}, "c3"),
        _final_reply("done"),
    ])

    with patch.object(core, "call_llm", lambda *_a, **_kw: next(responses)):
        with patch.object(tools, "execute", fake_execute, create=True):
            # conftest stubs tools.SCHEMAS to []; the validator then rejects
            # every tool name as "does not exist" and execute is never called.
            # Bypass validation here so the loop actually dispatches.
            with patch.object(core, "_validate_tool_args", lambda *_a, **_kw: None):
                list(agent._run_loop("trigger", streaming=False))

    # Disk was hit once; the rest were cache hits.
    assert execute_calls == 1
    tool_results = [m for m in agent.history if m.get("role") == "tool"]
    assert len(tool_results) == 3
    # First call: fresh content, no cache marker
    assert "harness-cached" not in tool_results[0]["content"]
    # Second and third: cache hits with the hint
    assert "harness-cached" in tool_results[1]["content"]
    assert "harness-cached" in tool_results[2]["content"]
    # The cache hit must take priority over the count-3 stuck-loop error
    # (because for read-only tools the cached result IS the right answer).
    assert "STUCK_LOOP" not in tool_results[2]["content"]
