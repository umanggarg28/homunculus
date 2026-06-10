"""call_llm survives malformed provider responses without crashing.

Live crash 2026-06-10 mid-state-machine: a provider returned 200 with
no `choices` key, the heartbeat tick KeyError'd on rj["choices"][0],
and the LeetCode delivery aborted with task_failure: "KeyError: 'choices'".

Fix: extract assistant message via a defensive helper; on malformed
shape (no choices / empty choices / no message), cool the provider
and fall through to the next one — same shape as a 5xx.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import core
from core import _extract_assistant_message


def test_extract_helper_returns_message_on_valid_shape():
    msg = {"role": "assistant", "content": "hi"}
    rj = {"choices": [{"message": msg}]}
    assert _extract_assistant_message(rj) is msg


def test_extract_helper_returns_none_on_missing_choices():
    """The exact failure mode from prod 2026-06-10."""
    assert _extract_assistant_message({"id": "x", "object": "chat"}) is None
    assert _extract_assistant_message({}) is None


def test_extract_helper_returns_none_on_empty_choices_list():
    assert _extract_assistant_message({"choices": []}) is None


def test_extract_helper_returns_none_on_missing_message():
    assert _extract_assistant_message({"choices": [{"finish_reason": "stop"}]}) is None


def test_extract_helper_returns_none_on_non_dict_input():
    assert _extract_assistant_message(None) is None
    assert _extract_assistant_message("not a dict") is None
    assert _extract_assistant_message([]) is None


# ---- integration: call_llm doesn't crash on missing choices ---------


def _mk_response(status_code=200, body=None, text=""):
    r = MagicMock()
    r.status_code = status_code
    r.text = text
    if body is not None:
        r.json.return_value = body
    else:
        r.json.side_effect = ValueError("not json")
    r.headers = {}
    return r


def test_call_llm_falls_through_on_missing_choices(monkeypatch):
    """A 200 response without `choices` must trigger provider cooldown
    + fallthrough, not a KeyError that kills the caller."""
    monkeypatch.setenv("HOMUNCULUS_API_KEY", "test-key")
    # First provider: 200 but no choices. Second: valid response.
    responses = iter([
        _mk_response(200, {"id": "x"}),                            # malformed
        _mk_response(200, {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}),
    ])

    def fake_post(*args, **kwargs):
        return next(responses)

    # Mock the provider chain to always return at least two providers.
    def fake_providers(model):
        return [
            ("https://prov1.example/v1/chat/completions", "k1", "model-a"),
            ("https://prov2.example/v1/chat/completions", "k2", "model-b"),
        ]

    with patch.object(core, "_providers", side_effect=fake_providers), \
         patch.object(core.httpx, "post", side_effect=fake_post):
        msg = core.call_llm(
            messages=[{"role": "user", "content": "x"}],
            tool_schemas=None,
        )
    assert msg == {"role": "assistant", "content": "ok"}


def test_call_llm_falls_through_on_non_json_200(monkeypatch):
    """A 200 that isn't valid JSON (some proxies do this) — same
    fallthrough behavior."""
    monkeypatch.setenv("HOMUNCULUS_API_KEY", "test-key")
    responses = iter([
        _mk_response(200, body=None, text="<html>oops</html>"),
        _mk_response(200, {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}),
    ])

    def fake_post(*args, **kwargs):
        return next(responses)

    def fake_providers(model):
        return [
            ("https://prov1.example/v1/chat/completions", "k1", "model-a"),
            ("https://prov2.example/v1/chat/completions", "k2", "model-b"),
        ]

    with patch.object(core, "_providers", side_effect=fake_providers), \
         patch.object(core.httpx, "post", side_effect=fake_post):
        msg = core.call_llm(
            messages=[{"role": "user", "content": "x"}],
            tool_schemas=None,
        )
    assert msg["content"] == "ok"
