"""Clarify-before-act gate.

Stress baseline probe #2: "Set it up." with no context made the agent invent a
task and spend 103s / 8 web calls. The gate returns a clarifying question before
the tool loop when the message is an ungrounded ambiguous imperative — and stays
out of the way otherwise.
"""

from __future__ import annotations

import sys
import types

if "homunculus.tools.notify" not in sys.modules:
    _stub = types.ModuleType("homunculus.tools.notify")
    _stub._send_to_telegram = lambda *_a, **_kw: None
    sys.modules["homunculus.tools.notify"] = _stub

from homunculus.core import _clarify_before_act  # noqa: E402


def test_bare_set_it_up_with_no_context_is_clarified():
    out = _clarify_before_act("Set it up.", [])
    assert out is not None and "what to act on" in out.lower()


def test_variants_are_clarified():
    for msg in ["do it", "handle this", "take care of that", "just sort it out", "deal with it"]:
        assert _clarify_before_act(msg, []) is not None, msg


def test_grounded_after_prior_turn_is_not_gated():
    history = [
        {"role": "user", "content": "I want a daily standup reminder at 9am"},
        {"role": "assistant", "content": "Sure, want me to create it?"},
    ]
    assert _clarify_before_act("set it up", history) is None


def test_specific_request_is_not_gated():
    assert _clarify_before_act("Set up a daily 9am standup reminder", []) is None


def test_question_is_not_gated():
    assert _clarify_before_act("What's the weather?", []) is None
