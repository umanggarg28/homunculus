"""Tests for Agent._output_guard().

The guard is a deterministic post-LLM filter that catches failure modes
before they reach the user. Each test covers one rule and verifies both
the pass and fail cases.

No LLM calls, no HTTP, no disk access. Pure function tests.
"""

import sys
import types
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Minimal stubs so core.py can be imported without a running MCP manager or
# LLM credentials. We stub `tools` and `events` at the module level before
# importing core.
# ---------------------------------------------------------------------------

def _make_tools_stub():
    mod = types.ModuleType("tools")
    mod.SCHEMAS = []
    mod.execute = lambda name, args: "ok"
    mod.get_mode = lambda: "build"
    mod.set_mode = lambda m: None
    mod.tool_names = lambda: set()
    return mod


def _make_events_stub():
    mod = types.ModuleType("events")
    mod.emit = lambda *a, **kw: None
    mod.truncate_preview = lambda s, n=120: str(s)[:n]
    return mod


sys.modules.setdefault("tools", _make_tools_stub())
sys.modules.setdefault("events", _make_events_stub())

# Also stub dotenv so the import doesn't fail in environments without it.
dotenv_stub = types.ModuleType("dotenv")
dotenv_stub.load_dotenv = lambda *a, **kw: None
sys.modules.setdefault("dotenv", dotenv_stub)

# Stub tasks module used by Agent._format_tasks / _local_status
tasks_stub = types.ModuleType("tasks")
class _FakeStore:
    def list(self, *a, **kw): return []
    def due(self): return []
tasks_stub.TaskStore = lambda *a, **kw: _FakeStore()
tasks_stub.ALLOWED_RECURRENCE = {"none", "daily", "weekly"}
sys.modules.setdefault("tasks", tasks_stub)

from core import Agent  # noqa: E402  (import after stubs)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FALLBACK = "I don't have enough context to answer that well. Could you give me more detail?"


def _guard(reply: str, tool_names_used: set[str] | None = None) -> str:
    agent = Agent.__new__(Agent)
    agent.memory = None
    agent.model = "test"
    agent.history = []
    clean, _violations = agent._output_guard(reply, tool_names_used or set())
    # Mirror the caller behaviour: None means guard fired → return FALLBACK
    return clean if clean is not None else FALLBACK


# ---------------------------------------------------------------------------
# Rule 1 — memory filename leak
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "The file project_delivered_leetcode_problems.md does not exist.",
    "Check feedback_no_emojis.md for the preference.",
    "See user_name.md in the index.",
    "I looked in reference_obsidian_vault.md but nothing matched.",
    "skill_send_daily_digest.md has the steps.",
])
def test_memory_filename_leak_blocked(text):
    assert _guard(text) == FALLBACK


@pytest.mark.parametrize("text", [
    "I couldn't find the leetcode tracker.",
    "The preference says no emojis.",
    "Your name is Umang.",
    "See the obsidian vault reference for details.",
    "Found 3 matching memories.",
    # Partial matches that should NOT trigger (no .md suffix)
    "project_delivered_leetcode_problems is referenced above.",
])
def test_memory_filename_leak_passes(text):
    assert _guard(text) == text


# ---------------------------------------------------------------------------
# Rule 2 — internal path leak
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "The log is at workspace/memory/logs/2026/05/log.md.",
    "memory/logs/2026/05/2026-05-01.md was read.",
    "I checked memory/_session.json for context.",
])
def test_internal_path_leak_blocked(text):
    assert _guard(text) == FALLBACK


def test_internal_path_clean():
    text = "I checked your conversation logs from yesterday."
    assert _guard(text) == text


# ---------------------------------------------------------------------------
# Rule 3 — error echo
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "ERROR: tool 'read_file' timed out.",
    "  ERROR: something went wrong",
    "ERROR running python: NameError: name 'x' is not defined",
])
def test_error_echo_blocked(text):
    assert _guard(text) == FALLBACK


@pytest.mark.parametrize("text", [
    "There was an issue fetching that URL.",
    "The tool returned an error message.",
    # ERROR mid-sentence (not at start) with no "ERROR running" — should pass
    "I found no ERROR in the logs.",
])
def test_error_echo_passes(text):
    assert _guard(text) == text


# ---------------------------------------------------------------------------
# Rule 4 — example.com confabulation (no web tools used)
# ---------------------------------------------------------------------------

def test_example_com_blocked_without_web_tools():
    text = "The page's title is 'Example Domain' — it's a placeholder."
    assert _guard(text, tool_names_used=set()) == FALLBACK


def test_example_com_passes_when_web_tool_used():
    text = "The page's title is 'Example Domain' — it's a placeholder."
    # If the agent actually called web_fetch, the answer is legitimate.
    assert _guard(text, tool_names_used={"web_fetch"}) == text


def test_example_domain_blocked():
    text = "This is the Example Domain — used in documentation."
    assert _guard(text, tool_names_used=set()) == FALLBACK


def test_unrelated_domain_passes():
    text = "The domain example.io is a real site."
    assert _guard(text, tool_names_used=set()) == text


# ---------------------------------------------------------------------------
# Clean reply — no rules should fire
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Your next LeetCode problem is Two Sum.",
    "I scheduled the task for 9am tomorrow.",
    "Done — I updated the weekly review file.",
    "The reflection ran successfully. 3 memories saved.",
    "",
    "   ",
])
def test_clean_reply_passes(text):
    assert _guard(text) == text


# ---------------------------------------------------------------------------
# Rule — confabulated success (claims an action worked while every tool failed)
# ---------------------------------------------------------------------------

from core import tool_result_indicates_failure  # noqa: E402


def _guard_with_outcomes(reply, tool_outcomes, tool_names_used=None):
    agent = Agent.__new__(Agent)
    agent.memory = None
    agent.model = "test"
    agent.history = []
    clean, violations = agent._output_guard(
        reply, tool_names_used or {o["name"] for o in tool_outcomes}, tool_outcomes
    )
    return clean, violations


def test_success_claim_blocked_when_all_tools_failed():
    # The live bug: propose_skill returned {"ok": false}, agent claimed success.
    reply = "I've filed a permanent skill proposal that runs every Monday."
    outcomes = [{"name": "propose_skill", "args": {}, "success": False}]
    clean, violations = _guard_with_outcomes(reply, outcomes)
    assert clean is None
    assert "success_claim_all_tools_failed" in violations


def test_success_claim_passes_when_a_tool_succeeded():
    # Conservative: if any tool succeeded, the claim may be about that.
    reply = "I've filed the proposal for review."
    outcomes = [
        {"name": "propose_skill", "args": {}, "success": False},
        {"name": "propose_skill", "args": {}, "success": True},
    ]
    clean, violations = _guard_with_outcomes(reply, outcomes)
    assert "success_claim_all_tools_failed" not in violations


def test_no_claim_phrase_does_not_fire():
    reply = "The proposal could not be filed — validation rejected the criteria."
    outcomes = [{"name": "propose_skill", "args": {}, "success": False}]
    clean, violations = _guard_with_outcomes(reply, outcomes)
    assert "success_claim_all_tools_failed" not in violations
    assert clean == reply


# ---- structured-failure recognition ------------------------------------


def test_tool_result_failure_recognises_ok_false_json():
    assert tool_result_indicates_failure('{\n  "ok": false,\n  "errors": ["x"]\n}') is True
    assert tool_result_indicates_failure('{"ok":false}') is True


def test_tool_result_failure_recognises_prefixes():
    assert tool_result_indicates_failure("ERROR: nope") is True
    assert tool_result_indicates_failure("BLOCKED: HTTP 403") is True


def test_tool_result_success_is_not_failure():
    assert tool_result_indicates_failure('{"ok": true, "id": "prop-0001"}') is False
    assert tool_result_indicates_failure("Saved skill v2.") is False
    assert tool_result_indicates_failure(None) is False
