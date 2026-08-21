"""The failure-sentinel registry is the only definition — enforced structurally.

These are contract checks over the repository rather than behaviour tests of
one function, in the same spirit as `skill_contracts.py`: they read the real
source and assert the parts agree. The bug they exist to prevent is a tool
inventing a spelling the recognizers do not know, which is invisible to every
behavioural test because each side is correct in isolation.

AST-parsing the tool sources (rather than importing them) keeps this runnable
under the conftest stub that replaces `homunculus.tools` with an empty module.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from homunculus.output_guard import tool_result_indicates_failure
from homunculus.sentinels import SENTINELS, find_sentinel, starts_with_sentinel
from homunculus.task_guard import _FAILURE_SENTINELS

TOOLS_DIR = Path(__file__).resolve().parent.parent / "homunculus" / "tools"

#: Any uppercase token ending in UNAVAILABLE, in either shape.
_LITERAL_RE = re.compile(r"\b[A-Z][A-Z_ ]*UNAVAILABLE\b")


def _sentinel_literals_in_tools() -> dict[str, list[str]]:
    """Every `*UNAVAILABLE` token appearing in a string literal under tools/.

    Docstrings are included deliberately: a tool description that promises a
    token the harness cannot recognise is the same bug seen from the model's
    side, and those descriptions are what the model is told to match on.
    """
    found: dict[str, list[str]] = {}
    for path in sorted(TOOLS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for token in _LITERAL_RE.findall(node.value):
                    found.setdefault(token.strip(), []).append(path.name)
    return found


def test_every_tool_sentinel_is_registered():
    unregistered = {
        token: sorted(set(files))
        for token, files in _sentinel_literals_in_tools().items()
        if token not in SENTINELS
    }
    assert not unregistered, (
        "These failure sentinels appear in homunculus/tools/ but are not in "
        "homunculus/sentinels.SENTINELS, so no guard recognises them and an "
        "outage from these tools reads as a successful run:\n"
        + "\n".join(f"  {tok!r} — {', '.join(files)}" for tok, files in unregistered.items())
    )


@pytest.mark.parametrize("sentinel", SENTINELS)
def test_registered_sentinel_is_recognised_as_failure(sentinel):
    """The property finding 1 violated: registered means detected, both shapes."""
    assert starts_with_sentinel(sentinel)
    assert tool_result_indicates_failure(f"{sentinel}: the source is down")


@pytest.mark.parametrize("sentinel", SENTINELS)
def test_registered_sentinel_is_blocked_from_delivery(sentinel):
    """Every sentinel is refused in a notify body, not just the two listed before."""
    assert sentinel in _FAILURE_SENTINELS
    assert find_sentinel(f"Good morning!\n\n{sentinel}: source down\n") == sentinel


def test_quoted_sentinel_is_not_a_failure():
    """Anchoring is load-bearing: reporting an outage is not suffering one."""
    quoted = "The log line from yesterday reads WEATHER UNAVAILABLE: no location."
    assert not starts_with_sentinel(quoted)
    assert not tool_result_indicates_failure(quoted)


def test_spaced_sentinels_are_present():
    """Regression pin for the exact bug: the space-separated shapes must be
    registered, since the old shape-based regex required an underscore."""
    for spaced in ("WEATHER UNAVAILABLE", "POSTING UNAVAILABLE", "CAREER CONTEXT UNAVAILABLE"):
        assert spaced in SENTINELS
        assert tool_result_indicates_failure(f"{spaced}: down")


# ------------------------------------------------------ the unreachable gate

def test_daily_brief_outage_blocks_completion():
    """The gate that was structurally unreachable for the flagship task.

    `every_required_source_failed` fires only when EVERY required source
    failed, and membership in `_failed_tools` comes from
    `tool_result_indicates_failure`. get_weather's sentinel is space-
    separated, so before the shared registry that call could never be seen
    as failed — which meant `all(...)` could never be true and a total
    outage of the daily brief's three sources still closed as a success.
    """
    from homunculus.task_guard import TaskGuard

    required = ["get_weather", "task_health_summary", "news_headlines"]
    guard = TaskGuard({"brief": []}, required_calls_by_task={"brief": required})

    # Drive the real call -> result sequence: the gate needs both the trace
    # (the source was exercised) and the outcome (it failed).
    for tool, result in (
        ("get_weather", "WEATHER UNAVAILABLE: forecast request failed (timeout)."),
        ("task_health_summary", "ERROR: health snapshot unavailable"),
        ("news_headlines", "NEWS_UNAVAILABLE: every configured feed failed."),
    ):
        guard.on_tool_call(tool, {})
        guard.observe_tool_result(tool, result)

    assert set(guard.failed_tools()) == set(required), (
        "a space-separated sentinel must register as a failure like any other"
    )
    assert sorted(guard.every_required_source_failed("brief")) == sorted(required)

    blocked = guard.on_tool_call("complete_task", {"task_id": "brief"})
    assert blocked is not None
    assert "complete_task blocked" in blocked
    # The refusal names the sources, so it steers the model to record_failure
    # rather than merely denying the call.
    assert "get_weather" in blocked and "record_failure" in blocked


def test_partial_outage_still_completes():
    """The gate stays narrow: something real to deliver means the run stands."""
    from homunculus.task_guard import TaskGuard

    required = ["get_weather", "news_headlines"]
    guard = TaskGuard({"brief": []}, required_calls_by_task={"brief": required})
    for tool, result in (
        ("get_weather", "WEATHER UNAVAILABLE: timeout."),
        ("news_headlines", "- [Real headline](https://x/1)"),
    ):
        guard.on_tool_call(tool, {})
        guard.observe_tool_result(tool, result)

    assert guard.every_required_source_failed("brief") == []


def test_notify_leak_checks_run_on_scheduled_delivery():
    """Gap C: notify() never passes through run_output_guard, so the leak
    checks a final reply gets are applied here too."""
    from homunculus.task_guard import TaskGuard

    guard = TaskGuard({"brief": []})
    blocked = guard.on_tool_call(
        "notify", {"text": "Here you go — see workspace/memory/skill_daily_brief.md"}
    )
    assert blocked is not None and "internal_path_leak" in blocked

    clean = guard.on_tool_call("notify", {"text": "Morning! Clear skies, 18°C."})
    assert clean is None
