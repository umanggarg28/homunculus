"""gpt-oss-* models get reasoning.effort=low to free up tokens for content.

Regression: switching the primary to openai/gpt-oss-120b made every
heartbeat tick return empty content + finish_reason=length because the
default high-reasoning mode burns the output token budget on internal
chain-of-thought. The agent loop already IS the reasoning structure
(each tick is a thought), so effort=low is correct for our use case.
"""

from __future__ import annotations

from homunculus.llm import _apply_reasoning_effort


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
    _apply_reasoning_effort(payload, "anthropic/claude-haiku-4.5")
    assert "reasoning" not in payload


def test_llama_unchanged() -> None:
    payload: dict = {}
    _apply_reasoning_effort(payload, "meta-llama/llama-3.3-70b-instruct:free")
    assert "reasoning" not in payload


def test_explicit_medium_effort_for_refinement() -> None:
    """Skill refinement passes effort='medium' so the model gets room
    to actually reason through API discovery and verification."""
    payload: dict = {}
    _apply_reasoning_effort(payload, "openai/gpt-oss-120b", effort="medium")
    assert payload["reasoning"] == {"effort": "medium"}


def test_explicit_high_effort_passes_through() -> None:
    payload: dict = {}
    _apply_reasoning_effort(payload, "openai/gpt-oss-120b", effort="high")
    assert payload["reasoning"] == {"effort": "high"}


def test_explicit_effort_ignored_on_non_gpt_oss() -> None:
    """Effort param is only meaningful for gpt-oss-*. For other models
    the helper still no-ops regardless of the requested effort."""
    payload: dict = {}
    _apply_reasoning_effort(payload, "gemini-2.5-flash", effort="high")
    assert "reasoning" not in payload


def test_reasoning_skipped_on_non_openrouter_urls() -> None:
    """Cerebras / Groq direct / Gemini direct reject unknown request
    params with HTTP 400. The `reasoning` field is an OpenRouter/OpenAI
    extension — silently omit it when the URL points elsewhere so
    fallback routing doesn't break with 400 ('reasoning is unsupported').
    """
    for url in (
        "https://api.cerebras.ai/v1/chat/completions",
        "https://api.groq.com/openai/v1/chat/completions",
        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
    ):
        payload: dict = {}
        _apply_reasoning_effort(payload, "openai/gpt-oss-120b", url=url, effort="medium")
        assert "reasoning" not in payload, (
            f"non-OpenRouter URL {url} should not get reasoning field"
        )


def test_reasoning_set_on_openrouter_url() -> None:
    payload: dict = {}
    _apply_reasoning_effort(
        payload, "openai/gpt-oss-120b",
        url="https://openrouter.ai/api/v1/chat/completions",
        effort="medium",
    )
    assert payload["reasoning"] == {"effort": "medium"}


def test_reasoning_set_when_url_omitted_for_backcompat() -> None:
    """The URL param defaults to '' so existing callers (tests without
    URL context) still get reasoning applied — only the negative URL
    check skips it."""
    payload: dict = {}
    _apply_reasoning_effort(payload, "openai/gpt-oss-120b", effort="low")
    assert payload["reasoning"] == {"effort": "low"}
