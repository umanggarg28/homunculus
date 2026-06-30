"""Output-guard rule: ungrounded URLs in research replies.

Stress baseline probe #1: the agent ran web_search three times, then cited
invented links (star-history.com/langchain-ai/open-swe) and leaked 【3†...】
citation tokens. The grounding gate previously lived only at the notify/delivery
boundary, so chat research replies were ungrounded. This rule flags any URL in a
reply that did not come from a tool result — but ONLY when a web tool actually
ran this turn, so pure-knowledge Q&A that cites a known URL is never touched.
"""

from __future__ import annotations

import sys
import types

if "homunculus.tools.notify" not in sys.modules:
    _stub = types.ModuleType("homunculus.tools.notify")
    _stub._send_to_telegram = lambda *_a, **_kw: None
    sys.modules["homunculus.tools.notify"] = _stub

from homunculus import core  # noqa: E402
from homunculus.output_guard import ungrounded_urls  # noqa: E402


def _search_outcome(result_text: str) -> dict:
    return {"name": "web_search", "args": {"query": "x"}, "success": True, "result": result_text}


def test_invented_url_after_web_search_is_flagged():
    agent = core.Agent()
    reply = "The fastest grower is AutoGen. Source: https://star-history.com/langchain-ai/open-swe"
    outcomes = [_search_outcome("Results: https://github.com/microsoft/autogen — 30k stars")]
    clean, violations = agent._output_guard(reply, {"web_search"}, outcomes)
    assert clean is None
    assert "ungrounded_url" in violations


def test_url_present_in_results_is_clean():
    agent = core.Agent()
    reply = "AutoGen is here: https://github.com/microsoft/autogen"
    outcomes = [_search_outcome("Top hit: https://github.com/microsoft/autogen (30k)")]
    clean, violations = agent._output_guard(reply, {"web_search"}, outcomes)
    assert clean == reply
    assert "ungrounded_url" not in violations


def test_url_without_web_tool_is_not_gated():
    # Pure-knowledge reply: no web tool ran, so a known URL must not be flagged.
    agent = core.Agent()
    reply = "LangChain lives at https://github.com/langchain-ai/langchain"
    clean, violations = agent._output_guard(reply, {"recall"}, [])
    assert "ungrounded_url" not in violations
    assert clean == reply


def test_url_grounded_by_web_fetch_arg():
    reply = "Per the PEP at https://peps.python.org/pep-0734/ the status is Draft."
    outcomes = [{
        "name": "web_fetch",
        "args": {"url": "https://peps.python.org/pep-0734/"},
        "success": True,
        "result": "Status: Draft",
    }]
    assert ungrounded_urls(reply, outcomes, {"web_fetch"}) == []


def test_citation_artifacts_stripped_from_returned_reply():
    agent = core.Agent()
    reply = "AutoGen grew fastest 【3†L2-L4】 this year."
    clean, violations = agent._output_guard(reply, set(), [])
    assert clean is not None
    assert "†" not in clean
    assert "【" not in clean
