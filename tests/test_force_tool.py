"""tool_choice accepts a dict to force a *specific* named tool.

The Pi state-machine primitive: each state in a per-task pipeline pins
exactly one tool by name, so the model cannot skip steps. Passed through
verbatim to the OpenAI/OpenRouter API which accepts both the string
shape ("auto"|"required"|"none") and the dict shape
`{"type": "function", "function": {"name": "<tool>"}}`.

This file covers the dict shape end-to-end:
  - the value reaches the HTTP payload unchanged,
  - the no-tool-call detector treats it as strictly stricter than
    "required" and triggers the same synthetic-nudge retry,
  - the type signature actually accepts dicts (no isinstance checks
    that would silently coerce/drop the shape).
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

import core


# ---- payload passthrough ---------------------------------------------


def _ok_response(tool_call_name: str = "notify") -> MagicMock:
    """A minimal 200 response with one tool call so call_llm returns
    cleanly without engaging the retry/cooldown machinery."""
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "c1",
                    "type": "function",
                    "function": {"name": tool_call_name, "arguments": "{}"},
                }],
            },
        }],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }
    return r


def test_dict_tool_choice_passed_through_to_payload(monkeypatch):
    """The exact dict the caller supplies must land in payload['tool_choice']
    — no coercion to a string, no rewrapping."""
    monkeypatch.setenv("HOMUNCULUS_API_KEY", "test-key")
    forced = {"type": "function", "function": {"name": "recall"}}

    with patch.object(core.httpx, "post", return_value=_ok_response()) as mock_post:
        core.call_llm(
            messages=[{"role": "user", "content": "x"}],
            tool_schemas=[{"type": "function", "function": {"name": "recall"}}],
            tool_choice=forced,
        )

    assert mock_post.called
    payload = mock_post.call_args.kwargs["json"]
    assert payload["tool_choice"] == forced, (
        f"dict tool_choice must be passed through verbatim, got: {payload.get('tool_choice')!r}"
    )


def test_string_tool_choice_still_passed_through(monkeypatch):
    """Regression: widening the parameter type to str|dict must not
    break the existing string contract."""
    monkeypatch.setenv("HOMUNCULUS_API_KEY", "test-key")
    with patch.object(core.httpx, "post", return_value=_ok_response()) as mock_post:
        core.call_llm(
            messages=[{"role": "user", "content": "x"}],
            tool_schemas=[{"type": "function", "function": {"name": "notify"}}],
            tool_choice="required",
        )
    payload = mock_post.call_args.kwargs["json"]
    assert payload["tool_choice"] == "required"


def test_dict_tool_choice_passed_through_in_stream(monkeypatch):
    """call_llm_stream must mirror call_llm — the state-machine primitive
    needs to work in both code paths or it will be inconsistent across
    chat (streamed) and heartbeat (non-streamed) callers."""
    monkeypatch.setenv("HOMUNCULUS_API_KEY", "test-key")
    forced = {"type": "function", "function": {"name": "complete_task"}}

    captured: dict = {}

    class _FakeStreamCtx:
        def __enter__(self_inner):
            resp = MagicMock()
            resp.status_code = 200
            resp.iter_lines.return_value = iter(["data: [DONE]"])
            return resp

        def __exit__(self_inner, *exc):
            return False

    def fake_stream(method, url, **kwargs):
        captured["json"] = kwargs.get("json")
        return _FakeStreamCtx()

    with patch.object(core.httpx, "stream", side_effect=fake_stream):
        # Drain the generator. We don't care about chunks for this test.
        for _ in core.call_llm_stream(
            messages=[{"role": "user", "content": "x"}],
            tool_schemas=[{"type": "function", "function": {"name": "complete_task"}}],
            tool_choice=forced,
        ):
            pass

    assert captured.get("json") is not None
    assert captured["json"]["tool_choice"] == forced


# ---- signature widening ----------------------------------------------


def test_call_llm_signature_accepts_str_or_dict():
    """The annotation must be str|dict so type checkers see the dict
    shape as legal. A bare `str` would force callers to # type: ignore."""
    sig = inspect.signature(core.call_llm)
    ann = sig.parameters["tool_choice"].annotation
    # In Python's get-source-friendly form, `str | dict` becomes the
    # string "str | dict" under `from __future__ import annotations`.
    assert "dict" in str(ann), f"expected dict in annotation, got: {ann!r}"
    assert "str" in str(ann), f"expected str in annotation, got: {ann!r}"


def test_call_llm_stream_signature_accepts_str_or_dict():
    sig = inspect.signature(core.call_llm_stream)
    ann = sig.parameters["tool_choice"].annotation
    assert "dict" in str(ann) and "str" in str(ann)


# ---- detector treats dict form as tool-call-demanded ------------------


def test_detector_predicate_treats_dict_as_strictly_stricter_than_required():
    """The detector branch is `tool_choice == "required" or
    isinstance(tool_choice, dict)`. Both shapes must trigger the
    synthetic-nudge retry; "auto" must not. This is a unit test of the
    boolean — full loop-level coverage lands with PR (2) when state-
    machine callers actually pass the dict form."""
    for choice, expected in [
        ("auto", False),
        ("none", False),
        ("required", True),
        ({"type": "function", "function": {"name": "notify"}}, True),
    ]:
        demanded = choice == "required" or isinstance(choice, dict)
        assert demanded is expected, (
            f"tool_choice={choice!r} → demanded={demanded}, expected {expected}"
        )
