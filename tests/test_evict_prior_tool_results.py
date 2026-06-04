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
    assert "evicted from prior turn" in history[3]["content"]
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


def test_multiple_tool_results_all_evicted():
    history = [
        _tool_msg("a", "first" * 100),
        _tool_msg("b", "second" * 100),
        _tool_msg("c", "third" * 100),
    ]
    assert _evict_prior_tool_results(history) == 3
    for msg in history:
        assert "evicted from prior turn" in msg["content"]
