"""Multi-step detector that forces a visible plan (plan_steps) on the first turn."""

from __future__ import annotations

import sys
import types

if "homunculus.tools.notify" not in sys.modules:
    _stub = types.ModuleType("homunculus.tools.notify")
    _stub._send_to_telegram = lambda *_a, **_kw: None
    sys.modules["homunculus.tools.notify"] = _stub

from homunculus.core import _looks_multistep  # noqa: E402


def test_research_and_summarise_each_is_multistep():
    assert _looks_multistep(
        "Research the 3 most-discussed open-source AI agent frameworks this week "
        "and give me a one-line summary of each with a link."
    )


def test_step_by_step_is_multistep():
    assert _looks_multistep("Plan a weekend trip to Goa step by step for me please")


def test_compound_research_then_write_is_multistep():
    assert _looks_multistep("Find the latest news on X and write me a short summary")


def test_simple_question_is_not_multistep():
    assert not _looks_multistep("What's the weather tomorrow?")


def test_short_imperative_is_not_multistep():
    assert not _looks_multistep("Remind me to call the dentist")


def test_single_action_is_not_multistep():
    assert not _looks_multistep("Search for the LeetCode two-sum problem")
