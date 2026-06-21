"""Efficiency pass — dynamic tool loading, prompt caching, rate-limit signal.

All three address the same symptom: per-call input grew to ~8K tokens
of overhead and we hit provider TPM caps several times per task. The
tests below pin each helper directly — no module reloading (other
tests in this suite stub support modules and any reload races with
pydantic's stateful internal caches).
"""

from __future__ import annotations

import pytest


# ── prompt caching ──────────────────────────────────────────────────


@pytest.fixture
def core_mod():
    """Import core fresh-enough for the helpers. core itself is never
    stubbed by other tests (they stub `tools`, `mcp`, etc.) so importing
    it directly is safe even after other suites have run."""
    from homunculus import core
    return core


def test_cache_control_added_for_openrouter(core_mod):
    msgs = [
        {"role": "system", "content": "you are an agent"},
        {"role": "user", "content": "hi"},
    ]
    out = core_mod._maybe_add_cache_control(
        msgs, "https://openrouter.ai/api/v1/chat/completions",
    )
    assert isinstance(out[0]["content"], list)
    block = out[0]["content"][0]
    assert block["type"] == "text"
    assert block["text"] == "you are an agent"
    assert block["cache_control"] == {"type": "ephemeral"}
    assert out[1] == {"role": "user", "content": "hi"}


def test_cache_control_added_for_anthropic(core_mod):
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "u"}]
    out = core_mod._maybe_add_cache_control(msgs, "https://api.anthropic.com/v1/messages")
    assert isinstance(out[0]["content"], list)


def test_cache_control_no_op_for_gemini(core_mod):
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "u"}]
    out = core_mod._maybe_add_cache_control(
        msgs,
        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
    )
    # Gemini's API doesn't honour cache_control; sending the structured
    # block would silently confuse some validators. Strings must pass
    # through unchanged.
    assert out[0] == {"role": "system", "content": "sys"}


def test_cache_control_no_op_without_system_message(core_mod):
    msgs = [{"role": "user", "content": "hi"}]
    out = core_mod._maybe_add_cache_control(msgs, "https://openrouter.ai/...")
    assert out == msgs


# ── rate-limit awareness ────────────────────────────────────────────


def test_cool_provider_updates_recent_signal(core_mod, monkeypatch):
    # Reset module state — other tests may have cooled providers.
    monkeypatch.setattr(core_mod, "_PROVIDER_LAST_COOLED_AT", 0.0)
    assert core_mod._recent_provider_cool_seconds() is None
    core_mod._cool_provider("https://x/y", "model-a", seconds=30)
    age = core_mod._recent_provider_cool_seconds()
    assert age is not None
    assert age < 2  # just happened


# ── dynamic tool loading (pure-function tests) ──────────────────────


def test_always_loaded_contains_load_tool():
    """The always-loaded set must include load_tool itself — without
    it, the agent can never grow its active set."""
    # Direct attribute access via the module; if `tools` is currently
    # stubbed (some other test ran first) this attribute won't exist
    # and the test skips rather than reporting a false failure.
    from homunculus import tools
    if not hasattr(tools, "ALWAYS_LOADED"):
        pytest.skip("tools module is stubbed by an earlier test; checked separately")
    assert "load_tool" in tools.ALWAYS_LOADED


def test_schemas_for_filters_by_name(monkeypatch):
    from homunculus import tools
    if not hasattr(tools, "schemas_for") or not hasattr(tools, "_manager"):
        pytest.skip("real tools module not loaded in this test environment")

    fake_schemas = [
        {"type": "function", "function": {"name": "read_file", "description": "read"}},
        {"type": "function", "function": {"name": "write_file", "description": "write"}},
        {"type": "function", "function": {"name": "obscure_tool", "description": "rare"}},
    ]
    monkeypatch.setattr(tools._manager, "schemas", lambda: fake_schemas, raising=False)
    out = tools.schemas_for({"read_file", "write_file"})
    names = {s["function"]["name"] for s in out}
    assert names == {"read_file", "write_file"}


def test_tool_overview_excludes_set(monkeypatch):
    from homunculus import tools
    if not hasattr(tools, "tool_overview") or not hasattr(tools, "_manager"):
        pytest.skip("real tools module not loaded in this test environment")

    fake_schemas = [
        {"type": "function", "function": {"name": "read_file", "description": "read a file"}},
        {"type": "function", "function": {"name": "git_blame", "description": "git blame"}},
        {"type": "function", "function": {"name": "browser_navigate", "description": "open page"}},
    ]
    monkeypatch.setattr(tools._manager, "schemas", lambda: fake_schemas, raising=False)
    rows = tools.tool_overview(exclude={"read_file"})
    names = {r["name"] for r in rows}
    assert names == {"git_blame", "browser_navigate"}
    for r in rows:
        assert isinstance(r["description"], str) and r["description"]
