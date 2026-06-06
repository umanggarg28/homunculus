"""Item 1 of the robustness plan — tool result trimming.

Regression: today's LeetCode tick failed because web_fetch dumped a 50 KB
GitHub README into history, leaving no token budget for the final LLM call
to compose the notify() + complete_task() decision.

The fix is a hard cap on tool-result content that lands in conversation
history. Full content is still preserved in _events.jsonl.
"""

from core import _trim_tool_result_for_history
from config import get_config

# The constant moved to HomunculusConfig.loop.tool_result_hard_cap_chars
# during the agent refactor. Tests keep a local alias for readability.
TOOL_RESULT_HARD_CAP = get_config().loop.tool_result_hard_cap_chars


def test_short_result_passes_through_unchanged():
    """Results under the cap must not be modified — the hint would be noise."""
    short = "short ok result"
    out = _trim_tool_result_for_history("read_file", short)
    assert out == short


def test_exactly_at_cap_passes_through_unchanged():
    """Boundary: exactly TOOL_RESULT_HARD_CAP chars is still untrimmed."""
    body = "x" * TOOL_RESULT_HARD_CAP
    out = _trim_tool_result_for_history("read_file", body)
    assert out == body


def test_oversized_result_is_trimmed_with_hint():
    """A result over the cap is head-truncated and the hint is appended.
    Uses search_files (not in per-tool overrides) so the test exercises
    the default cap path, not a per-tool override."""
    body = "L" * (TOOL_RESULT_HARD_CAP * 5)  # 30 KB
    out = _trim_tool_result_for_history("search_files", body)
    assert isinstance(out, str)
    assert len(out) < len(body)
    # Trimming reserves 200 chars for the hint
    assert "chars trimmed by harness" in out
    # The hint mentions specifically how many chars were dropped
    trimmed_count = len(body) - (TOOL_RESULT_HARD_CAP - 200)
    assert f"{trimmed_count:,}" in out
    # The trimmed content stays under the cap
    assert len(out) < TOOL_RESULT_HARD_CAP + 250  # cap + ~hint length


def test_per_tool_cap_is_tighter_for_web_fetch():
    """web_fetch returns whole pages; most callers only need the lead.
    Per-tool cap was added to prevent 6K of nav/footer dumping into
    history — the dominant remaining cost source after eviction."""
    body = "W" * 10_000
    out_default = _trim_tool_result_for_history("search_files", body)
    out_web_fetch = _trim_tool_result_for_history("web_fetch", body)
    assert len(out_web_fetch) < len(out_default), (
        "web_fetch must be capped tighter than the default"
    )


def test_tail_preserving_truncation_for_python():
    """python stdout often has the final value/exception trace at the
    end. Per-tool config opts python into head+tail truncation so the
    informative tail isn't lost."""
    body = ("BEGIN " + "x" * 8000 + " END_OF_OUTPUT_MARKER")
    out = _trim_tool_result_for_history("python", body)
    assert "BEGIN" in out
    assert "END_OF_OUTPUT_MARKER" in out
    assert "elided" in out


def test_non_string_result_passes_through():
    """Some tools return dicts or lists — don't try to trim those."""
    dict_result = {"key": "value", "data": list(range(100))}
    out = _trim_tool_result_for_history("get_world_state", dict_result)
    assert out is dict_result  # identity, not copy


def test_today_leetcode_failure_shape_is_prevented():
    """Replay the exact pattern from 2026-06-04 06:21:24.

    web_fetch returned a 50 KB README of LeetCode solutions. Before the
    fix that whole blob went into history; the final LLM call had no room
    to produce useful tool calls and bailed to the empty-content fallback.

    After the fix the history entry is bounded and the agent has room to
    reason about what to do next.
    """
    github_readme_size = 50_000  # what we saw in the wild
    github_payload = "# LeetCode Top Interview 150\n\n" + ("- problem detail\n" * 3000)
    assert len(github_payload) > github_readme_size  # confirm fixture is realistic

    out = _trim_tool_result_for_history("web_fetch", github_payload)
    # After trimming, the history entry must be MUCH smaller than the raw
    # payload — otherwise context bloat would still kill the final call.
    assert len(out) < TOOL_RESULT_HARD_CAP + 500
    # And the head of the original content is preserved so the agent knows
    # what it got.
    assert "LeetCode Top Interview 150" in out
