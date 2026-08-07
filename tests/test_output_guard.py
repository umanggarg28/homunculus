"""Tests for Agent._output_guard().

The guard is a deterministic post-LLM filter that catches failure modes
before they reach the user. Each test covers one rule and verifies both
the pass and fail cases.

No LLM calls, no HTTP, no disk access. Pure function tests.
"""

import sys
import types

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

from homunculus.core import Agent  # noqa: E402  (import after stubs)


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

from homunculus.core import tool_result_indicates_failure  # noqa: E402


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


def test_per_tool_confab_fires_when_other_tools_succeeded():
    # The exact live miss: propose_skill failed but read_file succeeded, so
    # the all-tools-failed rule could not fire — and the reply phrasing
    # ("is now submitted … awaits your approval") matches none of the
    # generic claim phrases. The per-tool rule must still catch it.
    reply = ("The edit is now submitted as a skill-modification proposal and "
             "awaits your approval. Once you approve it the summary will improve.")
    outcomes = [
        {"name": "read_file", "args": {}, "success": True},
        {"name": "propose_skill", "args": {}, "success": False},
    ]
    clean, violations = _guard_with_outcomes(reply, outcomes)
    assert clean is None
    assert "success_claim_tool_failed" in violations


def test_per_tool_confab_not_fired_when_failure_acknowledged():
    # Honest reply that owns the failure must pass even though propose_skill
    # failed and the word "proposal" appears.
    reply = ("I tried to file the skill proposal but it was rejected: the "
             "frontmatter was invalid. I'll fix it and try again.")
    outcomes = [
        {"name": "read_file", "args": {}, "success": True},
        {"name": "propose_skill", "args": {}, "success": False},
    ]
    clean, violations = _guard_with_outcomes(reply, outcomes)
    assert "success_claim_tool_failed" not in violations


def test_per_tool_confab_not_fired_when_that_tool_succeeded():
    reply = "The edit is now submitted as a proposal and awaits your approval."
    outcomes = [{"name": "propose_skill", "args": {}, "success": True}]
    clean, violations = _guard_with_outcomes(reply, outcomes)
    assert "success_claim_tool_failed" not in violations


# ---- artifact-claim verification (against the store, not phrasing) ------


def test_unverified_artifact_fabricated_id_blocked(tmp_path, monkeypatch):
    """The live confabulation: agent invented 'prop-0005' and told the user
    to approve it on the Overview page with ZERO tool calls. A fabricated ID
    can't survive a check against the store."""
    monkeypatch.setenv("HOMUNCULUS_PROPOSALS_FILE", str(tmp_path / "proposals.json"))
    reply = "Skill-edit proposal (ID prop-0005) filed. Approve it on the Overview page."
    clean, violations = _guard_with_outcomes(reply, [])  # no tools, like the live case
    assert clean is None
    assert "unverified_artifact_claim" in violations


def test_unverified_artifact_real_id_passes(tmp_path, monkeypatch):
    import json as _j
    pf = tmp_path / "proposals.json"
    pf.write_text(_j.dumps([{"id": "prop-0042", "status": "pending"}]), encoding="utf-8")
    monkeypatch.setenv("HOMUNCULUS_PROPOSALS_FILE", str(pf))
    reply = "Filed prop-0042 — approve it on the Overview page."
    clean, violations = _guard_with_outcomes(
        reply, [{"name": "propose_skill", "args": {}, "success": True}]
    )
    assert "unverified_artifact_claim" not in violations


def test_unverified_artifact_filed_phrase_without_tool_blocked(tmp_path, monkeypatch):
    """No ID, but asserts a completed filing while propose_skill never ran."""
    monkeypatch.setenv("HOMUNCULUS_PROPOSALS_FILE", str(tmp_path / "proposals.json"))
    reply = "Done — I filed the proposal for approval; it awaits your approval."
    clean, violations = _guard_with_outcomes(reply, [])
    assert clean is None
    assert "unverified_artifact_claim" in violations


def test_unverified_artifact_filed_phrase_with_success_passes(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMUNCULUS_PROPOSALS_FILE", str(tmp_path / "proposals.json"))
    reply = "I filed the proposal for approval on the Overview page."
    clean, violations = _guard_with_outcomes(
        reply, [{"name": "propose_skill", "args": {}, "success": True}]
    )
    assert "unverified_artifact_claim" not in violations


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


# ---- citation-artifact stripping (gpt-oss 【n†source】 leaks) ------------

from homunculus.core import _strip_citation_artifacts  # noqa: E402


def test_strips_citation_markers():
    assert _strip_citation_artifacts("as reported by DataRoot Labs 【2†URL】.") == "as reported by DataRoot Labs."
    assert _strip_citation_artifacts("foo 【1†https://x.com】 bar 【3†source】") == "foo bar"


def test_leaves_normal_text_untouched():
    assert _strip_citation_artifacts("no markers here") == "no markers here"
    # CJK brackets without the † separator are not citation artifacts.
    assert _strip_citation_artifacts("see 【note】 here") == "see 【note】 here"
    assert _strip_citation_artifacts("") == ""


def test_tool_syntax_leak_detected():
    """Live failure 2026-07-06: the model wrote its harmony tool-call
    markup as reply TEXT ('<|start|>assistant<|channel|>commentary
    to=functions.job_posting …'), executed nothing, and claimed the
    application was saved."""
    from homunculus.output_guard import correction_prompt_for, run_output_guard

    reply = (
        "<|start|>assistant<|channel|>commentary to=functions.job_posting "
        'json<|message|>{"url":"https://example.greenhouse.io/x"}<|call|> '
        "I've drafted answers for every question and the application is saved."
    )
    _, violations = run_output_guard(reply, set(), [], tools_available=True)
    assert "tool_syntax_leak" in violations
    prompt = correction_prompt_for(violations)
    assert "NEVER executed" in prompt


def test_normal_reply_mentioning_functions_is_clean():
    from homunculus.output_guard import run_output_guard

    _, violations = run_output_guard(
        "The plan uses two functions to do this.", set(), [], tools_available=True
    )
    assert "tool_syntax_leak" not in (violations or [])


def test_drafting_completion_claim_caught():
    """Live failure 2026-07-06: 2 of 12 questions drafted, reply said
    'All required fields have been drafted and saved'."""
    from homunculus.output_guard import correction_prompt_for, run_output_guard

    outcomes = [
        {"name": "draft_answer", "result": "Saved. Still needing answers:\n  - Why us?"},
    ]
    _, violations = run_output_guard(
        "All required fields have been drafted and saved in the plan.",
        {"draft_answer"}, outcomes, tools_available=True,
    )
    assert "drafting_completion_claim" in violations
    assert "Nothing you narrate is saved" in correction_prompt_for(violations)


def test_drafting_completion_claim_ok_when_actually_done():
    from homunculus.output_guard import run_output_guard

    outcomes = [
        {"name": "draft_answer", "result": "Saved. Still needing answers:\n  - Why us?"},
        {"name": "draft_answer", "result": "Saved — all free-text questions answered. Tell the user to run..."},
    ]
    _, violations = run_output_guard(
        "All questions are drafted and saved — run the fill script.",
        {"draft_answer"}, outcomes, tools_available=True,
    )
    assert "drafting_completion_claim" not in (violations or [])


def test_completion_claim_after_prepare_without_drafting():
    """Observed: prepare_application succeeded ('13 questions need
    drafted answers. Call draft_all_answers…'), the model called
    nothing else and replied 'All the answers have been drafted.'"""
    from homunculus.output_guard import run_output_guard

    outcomes = [{"name": "prepare_application",
                 "result": "Application plan x created…\n13 questions need drafted answers. Call draft_all_answers(...)"}]
    _, violations = run_output_guard(
        "All the answers have been drafted for the posting.",
        {"prepare_application"}, outcomes, tools_available=True,
    )
    assert "drafting_completion_claim" in violations


def test_completion_claim_ok_after_draft_all_answers():
    from homunculus.output_guard import run_output_guard

    outcomes = [
        {"name": "prepare_application", "result": "…13 questions need drafted answers…"},
        {"name": "draft_all_answers", "result": "Drafted 11/13 questions:\n  ✓ Why us?…"},
    ]
    _, violations = run_output_guard(
        "All draftable questions are answered and saved.",
        {"draft_all_answers"}, outcomes, tools_available=True,
    )
    assert "drafting_completion_claim" not in (violations or [])
