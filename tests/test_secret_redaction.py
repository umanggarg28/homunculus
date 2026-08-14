"""Provider credentials must never reach the event log.

The log is rendered in the web console and screenshots of that console are
committed to a public repository, so a key that lands in the log has a path
to the public internet. Nothing writes one deliberately — provider error
bodies and tool results are echoed verbatim, and some providers quote the
offending key back at you.
"""

from __future__ import annotations

import json

from homunculus.security import redact_secrets


# Built at runtime from an obviously-fake filler rather than written as
# literals: a realistic-looking key in source trips GitHub's push-protection
# scanner (it blocked this very file on the first attempt), and a fixture that
# looks real is a fixture someone will one day mistake for real. Low entropy
# also keeps the detectors quiet while still matching our own patterns.
_FILL = "NOTAREALKEY"


SAMPLES = {
    "google": "AQ." + _FILL * 3,
    "google-legacy": "AIza" + _FILL * 3,
    "google-oauth": "ya29." + _FILL * 3,
    "openrouter": "sk-or-v1-" + "0" * 32,
    "cerebras": "csk-" + _FILL * 3,
    "tavily": "tvly-" + _FILL * 3,
    "openai": "sk-" + _FILL * 3,
    "github": "gh" + "p_" + _FILL * 4,
    "slack": "xox" + "b-1234567890-" + _FILL,
    "telegram": "123456789:AA" + _FILL * 3,
}


def test_every_known_provider_shape_is_redacted():
    for label, secret in SAMPLES.items():
        out = redact_secrets(f"request failed with key {secret} — retry")
        assert secret not in out, f"{label} survived redaction"
        assert "[REDACTED:" in out, label


def test_the_current_google_format_is_covered():
    """The AQ. format is the live AI Studio shape; a redactor written only
    for the legacy AIza prefix walks straight past it."""
    out = redact_secrets(SAMPLES["google"])
    assert out == "[REDACTED:google]"


def test_bearer_header_is_redacted():
    token = "HEADER." + _FILL * 3
    out = redact_secrets(f"Authorization: Bearer {token}")
    assert token not in out
    assert "[REDACTED:bearer]" in out


def test_ordinary_text_is_untouched():
    for benign in (
        "the sk- prefix identifies OpenAI keys",
        "commit 3f2a1b9c8d7e6f5a4b3c2d1e0f9a8b7c6d5e4f3a",
        "https://example.com/path?q=abcdefghijklmnop",
        "AQ is a magazine",
        "",
    ):
        assert redact_secrets(benign) == benign


def test_marker_names_the_provider():
    """A redacted line still has to tell you what to rotate."""
    assert "google" in redact_secrets(SAMPLES["google"])
    assert "openrouter" in redact_secrets(SAMPLES["openrouter"])


def test_redacted_line_is_still_valid_json():
    """emit() redacts the serialized line, so the marker must not introduce
    quotes or backslashes that would corrupt the record."""
    record = {"event": "llm_call", "result": f"401 from provider: {SAMPLES['google']}"}
    line = redact_secrets(json.dumps(record, ensure_ascii=False))
    parsed = json.loads(line)
    assert SAMPLES["google"] not in line
    assert parsed["event"] == "llm_call"


def test_emit_redacts_before_writing(tmp_path, monkeypatch):
    """End to end through the real emit(), against a real file."""
    import importlib

    log = tmp_path / "_events.jsonl"
    monkeypatch.setenv("HOMUNCULUS_EVENTS_PATH", str(log))
    events = importlib.reload(importlib.import_module("homunculus.events"))

    events.emit("tool_result", name="web_fetch",
                result=f"upstream said: {SAMPLES['google']}")

    written = log.read_text(encoding="utf-8")
    assert SAMPLES["google"] not in written
    assert "[REDACTED:google]" in written
    assert json.loads(written.splitlines()[0])["name"] == "web_fetch"

    # Leave the module bound to the default path for other tests.
    monkeypatch.undo()
    importlib.reload(events)


def test_secret_nested_deep_in_args_is_still_caught(tmp_path, monkeypatch):
    """Redacting the serialized line — not each field — is what makes a
    credential buried inside nested tool args impossible to miss."""
    import importlib

    log = tmp_path / "_events.jsonl"
    monkeypatch.setenv("HOMUNCULUS_EVENTS_PATH", str(log))
    events = importlib.reload(importlib.import_module("homunculus.events"))

    events.emit("tool_call", name="web_post",
                args=json.dumps({"headers": {"auth": {"key": SAMPLES["openrouter"]}}}))

    assert SAMPLES["openrouter"] not in log.read_text(encoding="utf-8")

    monkeypatch.undo()
    importlib.reload(events)
