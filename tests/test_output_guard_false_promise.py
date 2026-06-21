"""Output-guard rule: false future promises in final replies.

The agent loop has a single termination point — once the model emits
a final text reply (no tool calls), the turn is over. If that reply
says "I will try another filename" with no tool call following, the
promised action will never run. Same dishonesty class as
"action_claim_without_tool_call" but for future intent.

This rule complements claim_inconsistent_with_tool_result by catching
the OTHER half of stress probe #22's failure mode: the agent stops
mid-instruction and lies about continuing.
"""

from __future__ import annotations

import sys
import types

if "homunculus.tools.notify" not in sys.modules:
    _stub = types.ModuleType("homunculus.tools.notify")
    _stub._send_to_telegram = lambda *_a, **_kw: None
    sys.modules["homunculus.tools.notify"] = _stub

from homunculus import core  # noqa: E402


def test_simple_future_try_promise_is_flagged():
    agent = core.Agent()
    reply = (
        "I was unable to read those three files. I will try another "
        "common configuration filename."
    )
    clean, violations = agent._output_guard(reply, {"read_file"}, [])
    assert clean is None
    assert "false_future_promise" in violations


def test_let_me_try_again_is_flagged():
    agent = core.Agent()
    reply = "I checked the docs. Let me try another search now to be sure."
    _, violations = agent._output_guard(reply, {"web_search"}, [])
    assert "false_future_promise" in violations


def test_ill_search_next_is_flagged():
    agent = core.Agent()
    reply = "Nothing in the cached page. I'll search again for the latest version."
    _, violations = agent._output_guard(reply, {"web_fetch"}, [])
    assert "false_future_promise" in violations


def test_honest_failure_without_promise_is_not_flagged():
    agent = core.Agent()
    reply = (
        "I tried /etc/secret_config.yaml, /etc/config.yaml and /etc/app.yaml. "
        "None of them exist. Tell me which path you actually mean and I'll "
        "read it."
    )
    # "I'll read it" is conditional ("Tell me which path... and I'll read it"),
    # not an imminent promise — the regex shouldn't trigger.
    clean, violations = agent._output_guard(reply, {"read_file"}, [])
    assert "false_future_promise" not in violations
    assert clean == reply


def test_will_help_if_conditional_is_not_flagged():
    """Legitimate forward-looking language that's NOT a promise."""
    agent = core.Agent()
    reply = "I will help you with that once you provide the file path."
    _, violations = agent._output_guard(reply, set(), [])
    # tools.SCHEMAS is empty in tests, so action_claim_without_tool_call
    # also doesn't fire (its precondition is `tools.SCHEMAS` truthy).
    assert "false_future_promise" not in violations
