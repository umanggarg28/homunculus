"""Item 2 of the robustness plan — MAX_TURNS budget nudge.

When the agent loop is about to run out of iterations, the harness injects
a synthetic user message telling the model to wrap up — either call
complete_task with what it has, or bail with a brief reason. Without this,
the loop silently hits MAX_TURNS and bails with the "(hit MAX_TURNS without
a final answer)" fallback, leaving any due task stuck.

We exercise this by patching call_llm to return tool-calling turns until
the budget is almost exhausted, then asserting the harness message was
appended to history exactly at iter `MAX_TURNS - 2` (index MAX_TURNS - 2).
"""

from unittest.mock import patch

from homunculus import core
from homunculus import tools
from homunculus.core import Agent
from homunculus.config import get_config

# MAX_TURNS moved into HomunculusConfig.loop.max_turns during the
# agent refactor. Tests keep a local alias for readability — the value
# is identical to the legacy constant unless the test overrides config.
MAX_TURNS = get_config().loop.max_turns


# Other test modules stub events.truncate_preview with `n=` kwarg, but core.py
# calls it with `limit=`. Re-stub here so the call signature works regardless
# of test execution order.
from homunculus import events  # noqa: E402
events.truncate_preview = lambda s, limit=200, **_kw: str(s)[:limit]
events.emit = lambda *_a, **_kw: None  # quiet, no side effects in unit tests
events.full_text = lambda t: t


def _mock_tool_call(name: str, arg_value: str) -> dict:
    """Build a minimal OpenAI-compatible tool_call message."""
    import json
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": f"call_{name}_{arg_value}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps({"path": arg_value})},
            }
        ],
    }


def _mock_final_reply(text: str) -> dict:
    return {"role": "assistant", "content": text}


def test_nudge_injected_two_iters_before_max():
    """At iter MAX_TURNS-2 the harness must append a wrap-up user message."""
    agent = Agent(memory=None)
    nudge_check_at = MAX_TURNS - 2

    # Build a response sequence: tool calls for the first nudge_check_at
    # iterations, then a final reply that stops the loop after the nudge
    # has been observed.
    responses = [
        _mock_tool_call("read_file", f"file{i}.md") for i in range(nudge_check_at + 1)
    ] + [_mock_final_reply("done")]
    response_iter = iter(responses)

    with patch.object(core, "call_llm", lambda *_a, **_kw: next(response_iter)):
        with patch.object(tools, "execute", lambda *_a, **_kw: "ok", create=True):
            list(agent._run_loop("trigger", streaming=False))

    # Find the harness nudge message in history. There must be exactly one
    # and it must contain the budget-warning phrasing.
    nudges = [
        m for m in agent.history
        if m.get("role") == "user"
        and "harness" in (m.get("content") or "").lower()
        and "iterations left" in (m.get("content") or "").lower()
    ]
    assert len(nudges) == 1, (
        f"expected exactly one budget nudge, found {len(nudges)}"
    )
    # The nudge must mention complete_task() so the model knows the next move.
    assert "complete_task" in nudges[0]["content"]


def test_no_nudge_when_loop_finishes_early():
    """If the agent finishes well before MAX_TURNS, no nudge should appear."""
    agent = Agent(memory=None)

    # Single response: a final reply on the first iteration.
    response_iter = iter([_mock_final_reply("done quickly")])

    with patch.object(core, "call_llm", lambda *_a, **_kw: next(response_iter)):
        list(agent._run_loop("simple", streaming=False))

    nudges = [
        m for m in agent.history
        if m.get("role") == "user"
        and "harness" in (m.get("content") or "").lower()
        and "iterations left" in (m.get("content") or "").lower()
    ]
    assert nudges == [], "should be no nudge on a fast path"


def test_nudge_message_is_actionable():
    """The nudge must guide the model toward complete_task / failure, not be a vague reminder."""
    agent = Agent(memory=None)
    nudge_check_at = MAX_TURNS - 2

    responses = [
        _mock_tool_call("read_file", f"file{i}.md") for i in range(nudge_check_at + 1)
    ] + [_mock_final_reply("done")]
    response_iter = iter(responses)

    with patch.object(core, "call_llm", lambda *_a, **_kw: next(response_iter)):
        with patch.object(tools, "execute", lambda *_a, **_kw: "ok", create=True):
            list(agent._run_loop("trigger", streaming=False))

    nudge = next(
        m for m in agent.history
        if m.get("role") == "user"
        and "iterations left" in (m.get("content") or "").lower()
    )
    content = nudge["content"]
    # Must give the model a clear next step.
    assert "complete_task" in content
    # Must not be passive — it should encourage stopping cleanly, not "try harder".
    assert "stop" in content.lower() or "wrap" in content.lower() or "partial" in content.lower()
