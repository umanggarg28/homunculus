"""Eager eviction of tool results from prior turns.

Tool results are only load-bearing for the assistant message that
immediately follows them. Once a turn finishes, keeping the full
payload in-context burns tokens linearly with conversation length.
Replace with a stub; preserve tool_call_id on the message itself so
the API's tool_call/tool_result pairing rule still holds.
"""

from core import _evict_prior_tool_results


def _tool_msg(call_id: str, content: str) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def test_evicts_tool_messages_and_returns_count():
    history = [
        {"role": "system", "content": "you are an agent"},
        {"role": "user", "content": "fetch X"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "a"}]},
        _tool_msg("a", "X" * 2000),
        {"role": "assistant", "content": "done"},
    ]
    n = _evict_prior_tool_results(history)
    assert n == 1
    assert "tool result evicted" in history[3]["content"]
    assert "2,000" in history[3]["content"]
    # tool_call_id must survive — required for API pairing.
    assert history[3]["tool_call_id"] == "a"


def test_is_idempotent():
    history = [_tool_msg("a", "payload")]
    assert _evict_prior_tool_results(history) == 1
    assert _evict_prior_tool_results(history) == 0
    assert _evict_prior_tool_results(history) == 0


def test_leaves_non_tool_messages_alone():
    history = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "a"},
    ]
    snapshot = [dict(m) for m in history]
    assert _evict_prior_tool_results(history) == 0
    assert history == snapshot


def test_handles_non_string_content_gracefully():
    """Some tools return dicts/lists; those skip the cap and skip eviction."""
    history = [{"role": "tool", "tool_call_id": "a", "content": {"k": "v"}}]
    assert _evict_prior_tool_results(history) == 0
    assert history[0]["content"] == {"k": "v"}


def test_keep_recent_protects_most_recent_n_tool_results():
    """In-loop eviction (keep_recent=2) must leave the two most recent
    tool messages full-fidelity and stub everything older. This is the
    contract that keeps per-call input from growing linearly with
    iteration count inside a single agent loop."""
    history = [
        _tool_msg("a", "A" * 500),
        _tool_msg("b", "B" * 500),
        _tool_msg("c", "C" * 500),
        _tool_msg("d", "D" * 500),
    ]
    evicted = _evict_prior_tool_results(history, keep_recent=2)
    assert evicted == 2
    # Oldest two stubbed
    assert "tool result evicted" in history[0]["content"]
    assert "tool result evicted" in history[1]["content"]
    # Newest two preserved
    assert history[2]["content"] == "C" * 500
    assert history[3]["content"] == "D" * 500


def test_keep_recent_zero_evicts_everything():
    """keep_recent=0 (the default — used between user turns) must stub
    every tool result with no exceptions."""
    history = [_tool_msg("a", "A" * 100), _tool_msg("b", "B" * 100)]
    assert _evict_prior_tool_results(history, keep_recent=0) == 2
    assert all("tool result evicted" in m["content"] for m in history)


def test_multiple_tool_results_all_evicted():
    history = [
        _tool_msg("a", "first" * 100),
        _tool_msg("b", "second" * 100),
        _tool_msg("c", "third" * 100),
    ]
    assert _evict_prior_tool_results(history) == 3
    for msg in history:
        assert "tool result evicted" in msg["content"]


def test_keep_recent_chars_protects_small_older_results():
    """Size-aware retention: with a char budget, several SMALL older results
    survive (a 3-source brief can compose) even though keep_recent=2. The
    regression: keep_recent=2 alone dropped the brief's first source mid-loop,
    and STUCK_LOOP then blocked re-fetching it."""
    history = [
        _tool_msg("a", "A" * 500),   # commitments
        _tool_msg("b", "B" * 200),   # weather
        _tool_msg("c", "C" * 800),   # HN
        _tool_msg("d", "D" * 300),   # something else
    ]
    # Budget easily fits all four small results → nothing evicted.
    assert _evict_prior_tool_results(history, keep_recent=2, keep_recent_chars=20000) == 0
    assert all("evicted" not in m["content"] for m in history)


def test_keep_recent_chars_still_caps_large_payloads():
    """A large recent payload exhausts the budget, so older results beyond the
    keep_recent floor are still stubbed."""
    history = [
        _tool_msg("a", "A" * 5000),   # old, should evict (over budget)
        _tool_msg("b", "B" * 9000),   # big recent
        _tool_msg("c", "C" * 9000),   # big recent
    ]
    # keep_recent=2 protects b,c (18000); budget 20000 leaves 2000 — a's 5000
    # doesn't fit → evicted.
    assert _evict_prior_tool_results(history, keep_recent=2, keep_recent_chars=20000) == 1
    assert "evicted" in history[0]["content"]
    assert history[1]["content"] == "B" * 9000
    assert history[2]["content"] == "C" * 9000
