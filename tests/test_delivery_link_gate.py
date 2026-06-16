"""Delivery-tool link gate — the (B) verification, wired into the agent loop.

A notify() whose text carries a link that did NOT come from a tool result this
turn is rejected before dispatch (the fabricated-news bug rides in the notify
payload, not the final reply). A notify() whose links all came from a real tool
result this turn goes through. This is the in-loop counterpart to the
_unverified_urls unit tests.
"""

import json
from unittest.mock import patch

import core
import tools
from core import Agent

import events  # noqa: E402
events.truncate_preview = lambda s, limit=200, **_kw: str(s)[:limit]
events.emit = lambda *_a, **_kw: None
events.full_text = lambda t: t


def _tool_call_msg(name, args, call_id="c1"):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)},
        }],
    }


def _final_reply(text):
    return {"role": "assistant", "content": text}


def _run(agent, responses, fake_execute):
    with patch.object(core, "call_llm", lambda *_a, **_kw: next(responses)):
        with patch.object(tools, "execute", fake_execute, create=True):
            with patch.object(core, "_validate_tool_args", lambda *_a, **_kw: None):
                list(agent._run_loop("trigger", streaming=False))


def test_notify_with_fabricated_link_is_blocked():
    agent = Agent(memory=None)
    executed = []

    def fake_execute(name, args):
        executed.append((name, args))
        return "delivered"

    # The model invents a link nothing ever returned.
    responses = iter([
        _tool_call_msg("notify", {"text": "Top story: https://news.example/fabricated"}),
        _final_reply("done"),
    ])
    _run(agent, responses, fake_execute)

    # notify was NEVER dispatched — the gate rejected it pre-execute.
    assert ("notify", {"text": "Top story: https://news.example/fabricated"}) not in executed
    # The model saw a structured rejection naming the bad link.
    tool_results = [m for m in agent.history if m.get("role") == "tool"]
    assert any(
        "did not come from any tool result" in m["content"]
        and "news.example/fabricated" in m["content"]
        for m in tool_results
    )


def test_notify_with_grounded_link_passes():
    agent = Agent(memory=None)
    executed = []

    def fake_execute(name, args):
        executed.append((name, args))
        if name == "news_headlines":
            return "- [Real story](https://news.example/real)"
        return "delivered"

    # First fetch real links, THEN notify using one of them verbatim.
    responses = iter([
        _tool_call_msg("news_headlines", {"limit": 3}, "c1"),
        _tool_call_msg("notify", {"text": "Headline: https://news.example/real"}, "c2"),
        _final_reply("done"),
    ])
    _run(agent, responses, fake_execute)

    # notify actually ran — its link was grounded by the news_headlines result.
    assert ("notify", {"text": "Headline: https://news.example/real"}) in executed


def test_notify_without_links_passes():
    agent = Agent(memory=None)
    executed = []

    def fake_execute(name, args):
        executed.append((name, args))
        return "delivered"

    responses = iter([
        _tool_call_msg("notify", {"text": "Morning, Umang. Nothing scheduled today."}),
        _final_reply("done"),
    ])
    _run(agent, responses, fake_execute)

    assert ("notify", {"text": "Morning, Umang. Nothing scheduled today."}) in executed
