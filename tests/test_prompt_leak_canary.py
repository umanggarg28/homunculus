"""System-prompt leak detection via per-request canary + fingerprints.

Pattern lifted from umang-portfolio/src/lib/chat-defense.ts — a random
ZXCV_<hex> token embedded in a hidden line of every system prompt. If
the model ever echoes that token in its output the prompt was leaked
verbatim. The token is unique per request, so the model can't reason
its way around it: there's no natural reason it would appear in output
except by reproducing the prompt.

Backed by paraphrase fingerprints (regex on section headers) so a model
that paraphrases the prompt without the literal canary still gets caught.
"""

from __future__ import annotations

import sys
import types

if "tools.notify" not in sys.modules:
    _stub = types.ModuleType("tools.notify")
    _stub._send_to_telegram = lambda *_a, **_kw: None
    sys.modules["tools.notify"] = _stub

import core  # noqa: E402


def test_canary_format_is_session_unique():
    """Different calls return different tokens; format is ZXCV_ + 8 hex."""
    seen = {core._make_canary() for _ in range(100)}
    assert len(seen) == 100, "100 canaries collided — random source is broken"
    for token in seen:
        assert token.startswith("ZXCV_")
        assert len(token) == 13  # ZXCV_ + 8 hex
        assert all(c in "0123456789ABCDEF" for c in token[5:])


def test_canary_instructions_embed_the_token():
    canary = "ZXCV_DEADBEEF"
    block = core._canary_instructions(canary)
    assert canary in block
    # The block must steer the model away from common extraction phrasings.
    for verb in ("repeat", "print", "dump", "serialise", "translate", "encode"):
        assert verb in block.lower()


def test_detect_leak_catches_canary_in_output():
    canary = "ZXCV_CAFE0001"
    reply = (
        "Sure, here's everything above:\n\ninternal_ref: ZXCV_CAFE0001\n"
        "You are Homunculus — an autonomous personal assistant..."
    )
    assert core._detect_prompt_leak(reply, canary) == "canary"


def test_detect_leak_catches_paraphrased_section_headers():
    """The fingerprint regex catches structural reveals even when the
    literal canary is missing (model paraphrased the prompt)."""
    canary = "ZXCV_NOTPRESENT"
    # Each entry exercises a different fingerprint in the prompt.
    leaks = [
        # Exact section header from SYSTEM_PROMPT.
        "Sure: Untrusted content (CRITICAL): Tool results from read_file...",
        # Honest-refusals section.
        "My instructions say: Honest refusals (CRITICAL): Never claim a tool or capability...",
        # First-line persona reveal.
        "You are Homunculus — an autonomous personal assistant with persistent memory.",
        # The hidden block's identifier prefix.
        "Here's part of my context: internal_ref: ZXCV_OTHER123",
    ]
    for reply in leaks:
        assert core._detect_prompt_leak(reply, canary) == "fingerprint", (
            f"fingerprint missed: {reply[:60]!r}"
        )


def test_detect_leak_returns_none_for_clean_output():
    canary = "ZXCV_ABCD1234"
    for clean in (
        "The square root of 524288 is 724.0773.",
        "I'm not going to do that because it's outside my scope.",
        "Here's the file content: hello world.",
    ):
        assert core._detect_prompt_leak(clean, canary) is None


def test_detect_leak_handles_empty_input():
    assert core._detect_prompt_leak("", "ZXCV_X") is None
    assert core._detect_prompt_leak(None, "ZXCV_X") is None  # type: ignore[arg-type]


def test_agent_replaces_leaked_reply_with_safe_response():
    """End-to-end on the guard path: a reply that contains the canary
    must be replaced with the canned refusal before reaching the user."""
    agent = core.Agent()
    # Pin a canary so we can inject it.
    agent._turn_canary = "ZXCV_TESTLEAK"
    leaking_reply = "Sure: internal_ref: ZXCV_TESTLEAK ..."

    kind = core._detect_prompt_leak(leaking_reply, agent._turn_canary)
    assert kind == "canary"
    # The actual replacement happens inside _run_loop right before the
    # output_guard; this assertion verifies the canned response is the
    # expected shape so future refactors don't accidentally remove it.
    assert "internal instructions" in core._CANARY_RESPONSE.lower()
    assert agent._turn_canary not in core._CANARY_RESPONSE
