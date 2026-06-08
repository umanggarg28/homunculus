"""gpt-oss-* models get reasoning.effort=low to free up tokens for content.

Regression: switching the primary to openai/gpt-oss-120b made every
heartbeat tick return empty content + finish_reason=length because the
default high-reasoning mode burns the output token budget on internal
chain-of-thought. The agent loop already IS the reasoning structure
(each tick is a thought), so effort=low is correct for our use case.
"""

from __future__ import annotations

from core import _apply_reasoning_effort


def test_gpt_oss_120b_gets_low_reasoning_effort() -> None:
    payload: dict = {"model": "x", "messages": []}
    _apply_reasoning_effort(payload, "openai/gpt-oss-120b")
    assert payload["reasoning"] == {"effort": "low"}


def test_gpt_oss_free_route_also_gets_low_effort() -> None:
    payload: dict = {}
    _apply_reasoning_effort(payload, "openai/gpt-oss-120b:free")
    assert payload["reasoning"] == {"effort": "low"}


def test_gpt_oss_20b_also_gets_low_effort() -> None:
    payload: dict = {}
    _apply_reasoning_effort(payload, "openai/gpt-oss-20b")
    assert payload["reasoning"] == {"effort": "low"}


def test_gemini_unchanged() -> None:
    """Reasoning param only targets gpt-oss-*; sending it to Gemini
    would either be ignored or cause an error."""
    payload: dict = {"model": "gemini-2.5-flash"}
    _apply_reasoning_effort(payload, "gemini-2.5-flash")
    assert "reasoning" not in payload


def test_claude_unchanged() -> None:
    payload: dict = {}
    _apply_reasoning_effort(payload, "anthropic/claude-haiku-4-5")
    assert "reasoning" not in payload


def test_llama_unchanged() -> None:
    payload: dict = {}
    _apply_reasoning_effort(payload, "meta-llama/llama-3.3-70b-instruct:free")
    assert "reasoning" not in payload
