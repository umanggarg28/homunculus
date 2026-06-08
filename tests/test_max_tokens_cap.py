"""OpenRouter requests set an explicit max_tokens for credit-reservation.

Live refinement run on Haiku 4.5 via OpenRouter 402'd:

  "This request requires more credits, or fewer max_tokens. You
   requested up to 64000 tokens, but can only afford 6477."

OpenRouter reserves credit up to the model's max output (~64K for
Anthropic-family) when max_tokens is unset, even though real outputs
are typically <2K. The fix is an explicit modest cap.

Only applied for OpenRouter URLs; direct provider routes (Gemini,
Groq, Cerebras) don't reserve credit and can stay at their defaults.
"""

from __future__ import annotations

import core


def test_openrouter_gets_max_tokens_cap() -> None:
    payload: dict = {}
    core._apply_max_tokens(
        payload, "https://openrouter.ai/api/v1/chat/completions",
        "anthropic/claude-haiku-4-5",
    )
    assert payload["max_tokens"] == 4096


def test_openrouter_preserves_explicit_max_tokens() -> None:
    """If a caller already set max_tokens (e.g. summarisation paths
    that intentionally want a shorter reply), don't clobber it."""
    payload: dict = {"max_tokens": 200}
    core._apply_max_tokens(
        payload, "https://openrouter.ai/api/v1/chat/completions",
        "anthropic/claude-haiku-4-5",
    )
    assert payload["max_tokens"] == 200


def test_non_openrouter_urls_unchanged() -> None:
    """Gemini / Groq / Cerebras direct routes don't have the credit-
    reservation behavior; leave their defaults alone."""
    for url in (
        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "https://api.groq.com/openai/v1/chat/completions",
        "https://api.cerebras.ai/v1/chat/completions",
    ):
        payload: dict = {}
        core._apply_max_tokens(payload, url, "any-model")
        assert "max_tokens" not in payload, (
            f"non-openrouter URL {url} should not get a max_tokens cap"
        )
