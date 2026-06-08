"""Skill refinement runner — PR 2 of skill self-refinement.

Tests cover:
  - The refinement prompt is correctly assembled from current body +
    failure context (pure function, no agent needed)
  - save_refined_skill writes via the registry with source="refinement-tick"
  - save_refined_skill refuses outside a refinement run
  - save_refined_skill refuses obviously-bad input (empty body, wrong type)
  - abandon_refinement records the outcome without touching the registry
  - End-to-end refine_skill() with a stub Agent that calls save_refined_skill
    produces a "saved" RefinementResult and updates the on-disk skill
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from memory import Memory
from skills import Skills


# conftest stubs the top-level `tools` as a flat module to keep MCP
# deps optional. tools/_state.py and tools/skill_refinement.py both
# need to be loadable as real modules and address each other. Load
# _state first under the canonical name (`tools._state`) so that
# `tools.skill_refinement` finds it via `from . import _state`.
def _load_real_tool_submodule(name: str):
    src = Path(__file__).parent.parent / "tools" / f"{name}.py"
    full = f"tools.{name}"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(full, src)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # Pretend `tools` is a real package so relative imports resolve.
    if "tools" in sys.modules and not hasattr(sys.modules["tools"], "__path__"):
        sys.modules["tools"].__path__ = [str(src.parent)]  # type: ignore[attr-defined]
    sys.modules[full] = mod
    spec.loader.exec_module(mod)
    return mod


tool_state = _load_real_tool_submodule("_state")
refine_tools = _load_real_tool_submodule("skill_refinement")


from skill_refiner import (
    RefinementResult,
    build_refinement_prompt,
    refine_skill,
)


# ---- prompt builder ---------------------------------------------------


def test_prompt_includes_skill_name_and_current_body() -> None:
    p = build_refinement_prompt(
        skill_name="skill_x",
        current_body="step 1: foo\nstep 2: bar",
        failure_context="3 consecutive 403s on example.com",
        max_turns=30,
    )
    assert "skill_x" in p
    assert "step 1: foo" in p
    assert "step 2: bar" in p
    assert "3 consecutive 403s on example.com" in p
    # The prompt must reference both end-state tools so the model knows
    # which contract it's running under.
    assert "save_refined_skill" in p
    assert "abandon_refinement" in p


def test_prompt_handles_missing_current_body() -> None:
    """First-ever refinement on a non-existent skill (rare but valid):
    the prompt must not crash on empty body."""
    p = build_refinement_prompt(
        skill_name="skill_brand_new",
        current_body="",
        failure_context="user manually requested",
        max_turns=20,
    )
    assert "skill_brand_new" in p
    assert "brand-new skill" in p


def test_prompt_mentions_iteration_budget() -> None:
    p = build_refinement_prompt("skill_x", "body", "ctx", max_turns=42)
    assert "42" in p


# ---- save_refined_skill ----------------------------------------------


def test_save_refined_skill_refuses_outside_refinement_run() -> None:
    # Clean slate
    tool_state._skills = None  # type: ignore[attr-defined]
    out = refine_tools.save_refined_skill(
        "skill_x", "a" * 100, "rationale"
    )
    assert "ERROR" in out
    assert "refinement" in out.lower()


def test_save_refined_skill_refuses_empty_body(tmp_path: Path) -> None:
    tool_state._skills = Skills(tmp_path)  # type: ignore[attr-defined]
    try:
        out = refine_tools.save_refined_skill("skill_x", "", "rationale")
        assert "ERROR" in out
        assert "too short" in out
    finally:
        tool_state._skills = None  # type: ignore[attr-defined]


def test_save_refined_skill_refuses_too_short_body(tmp_path: Path) -> None:
    """A 5-character body is almost certainly a stub. The runner's
    purpose is to produce a real procedure, not a placeholder."""
    tool_state._skills = Skills(tmp_path)  # type: ignore[attr-defined]
    try:
        out = refine_tools.save_refined_skill("skill_x", "TODO", "rationale")
        assert "ERROR" in out
    finally:
        tool_state._skills = None  # type: ignore[attr-defined]


def test_save_refined_skill_refuses_invalid_name(tmp_path: Path) -> None:
    tool_state._skills = Skills(tmp_path)  # type: ignore[attr-defined]
    try:
        out = refine_tools.save_refined_skill(
            "bad name with spaces", "x" * 100, "rationale",
        )
        assert "ERROR" in out
        assert "invalid skill name" in out
    finally:
        tool_state._skills = None  # type: ignore[attr-defined]


def test_save_refined_skill_writes_via_registry_with_correct_source(tmp_path: Path) -> None:
    skills = Skills(tmp_path)
    tool_state._skills = skills  # type: ignore[attr-defined]
    try:
        body = "## How to deliver\n\n1. step a\n2. step b\n3. step c\n4. step d\n5. step e"
        out = refine_tools.save_refined_skill("skill_x", body, "switched to graphql")
        assert "Saved skill_x v1" in out
        # On-disk
        assert skills.load("skill_x") == body
        # Version metadata captures source = refinement-tick
        versions = skills.versions("skill_x")
        assert versions[0]["source"] == "refinement-tick"
        assert versions[0]["rationale"] == "switched to graphql"
    finally:
        tool_state._skills = None  # type: ignore[attr-defined]


def test_save_refined_skill_records_outcome(tmp_path: Path) -> None:
    """The runner reads _refinement_outcome to know what the agent did."""
    skills = Skills(tmp_path)
    tool_state._skills = skills  # type: ignore[attr-defined]
    tool_state._refinement_outcome = None  # type: ignore[attr-defined]
    try:
        refine_tools.save_refined_skill(
            "skill_x", "body that is plenty long " * 5, "rationale here",
        )
        outcome = tool_state._refinement_outcome
        assert isinstance(outcome, RefinementResult)
        assert outcome.outcome == "saved"
        assert outcome.new_version == 1
        assert outcome.rationale == "rationale here"
    finally:
        tool_state._skills = None  # type: ignore[attr-defined]
        tool_state._refinement_outcome = None  # type: ignore[attr-defined]


# ---- abandon_refinement ----------------------------------------------


def test_abandon_refinement_does_not_touch_registry(tmp_path: Path) -> None:
    skills = Skills(tmp_path)
    skills.save("skill_x", "original body", source="bootstrap")
    tool_state._skills = skills  # type: ignore[attr-defined]
    tool_state._refinement_outcome = None  # type: ignore[attr-defined]
    try:
        out = refine_tools.abandon_refinement("tried 3 mirrors, all blocked")
        assert "abandoned" in out.lower()
        # Original body is preserved
        assert skills.load("skill_x") == "original body"
        assert skills.current_version("skill_x") == 1
        # Outcome recorded for the runner
        outcome = tool_state._refinement_outcome
        assert isinstance(outcome, RefinementResult)
        assert outcome.outcome == "abandoned"
        assert "3 mirrors" in outcome.reason
    finally:
        tool_state._skills = None  # type: ignore[attr-defined]
        tool_state._refinement_outcome = None  # type: ignore[attr-defined]


def test_abandon_refinement_truncates_long_reason(tmp_path: Path) -> None:
    """Don't let an unbounded reason eat the event log."""
    tool_state._skills = Skills(tmp_path)  # type: ignore[attr-defined]
    tool_state._refinement_outcome = None  # type: ignore[attr-defined]
    try:
        refine_tools.abandon_refinement("x" * 2000)
        outcome = tool_state._refinement_outcome
        assert isinstance(outcome, RefinementResult)
        assert len(outcome.reason) == 500
    finally:
        tool_state._skills = None  # type: ignore[attr-defined]
        tool_state._refinement_outcome = None  # type: ignore[attr-defined]


# ---- end-to-end via stubbed Agent ------------------------------------


def test_refine_skill_with_stubbed_agent_saves(tmp_path: Path) -> None:
    """End-to-end: refine_skill() runs an Agent (here stubbed to immediately
    call save_refined_skill) and returns a 'saved' RefinementResult.
    Exercises the runner's prompt construction + tool_state wiring +
    outcome read-back path."""
    mem = Memory(tmp_path)
    Skills(tmp_path).save("skill_x", "old body", source="bootstrap")

    # Stub the Agent to simulate the refinement model calling
    # save_refined_skill once. We patch core.Agent so refine_skill's
    # internal import picks up the stub.
    def fake_chat(self, user_message, source="web"):
        assert source == "refinement"
        # Simulate the tool call landing in the MCP layer.
        refine_tools.save_refined_skill(
            "skill_x",
            "## New procedure\n\n1. Use the official graphql endpoint at example.com/graphql/\n"
            "2. Query studyPlanV2Detail(planSlug='...')\n"
            "3. Format the response into notify()\n"
            "4. Call complete_task with the result.",
            "Replaced fragile scraping with the official graphql endpoint; verified the query returns the full list.",
        )
        return "Saved skill_x v2."

    class FakeAgent:
        def __init__(self, memory=None, system_prompt="", model=None):
            self.memory = memory
            self.history = [{"role": "system", "content": system_prompt}]
        chat = fake_chat

    with patch("core.Agent", FakeAgent):
        result = refine_skill(
            skill_name="skill_x",
            failure_context="3 consecutive 403s on example.com",
            memory=mem,
            max_turns=30,
        )

    assert result.outcome == "saved"
    assert result.new_version == 2  # v1 was bootstrap, v2 is the refinement
    assert "graphql" in result.rationale.lower()
    # On-disk: v2 replaced v1
    assert "graphql" in (Skills(tmp_path).load("skill_x") or "")


def test_refine_skill_exhausted_when_neither_tool_called(tmp_path: Path) -> None:
    """If the refinement Agent exits without calling save or abandon,
    the runner reports outcome=exhausted and the registry is untouched."""
    mem = Memory(tmp_path)
    Skills(tmp_path).save("skill_x", "old body", source="bootstrap")

    def fake_chat(self, user_message, source="web"):
        return "I gave up but forgot to call abandon_refinement"

    class FakeAgent:
        def __init__(self, memory=None, system_prompt="", model=None):
            self.memory = memory
            self.history = [{"role": "system", "content": system_prompt}]
        chat = fake_chat

    with patch("core.Agent", FakeAgent):
        result = refine_skill(
            skill_name="skill_x",
            failure_context="some context",
            memory=mem,
            max_turns=10,
        )

    assert result.outcome == "exhausted"
    assert Skills(tmp_path).load("skill_x") == "old body"
    assert Skills(tmp_path).current_version("skill_x") == 1
