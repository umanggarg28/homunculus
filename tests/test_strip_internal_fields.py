"""Internal-only message fields (e.g. `source`) must be stripped before
messages go on the wire to the LLM provider. Some providers reject
unknown keys outright; the rest just waste tokens."""

from core import _strip_internal_fields


def test_strips_source_field():
    msgs = [
        {"role": "user", "content": "hi", "source": "telegram"},
        {"role": "assistant", "content": "hey", "source": "telegram"},
    ]
    out = _strip_internal_fields(msgs)
    assert out == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hey"},
    ]


def test_leaves_provider_fields_intact():
    msgs = [
        {"role": "assistant", "content": None, "tool_calls": [{"id": "a"}], "source": "web"},
        {"role": "tool", "tool_call_id": "a", "content": "result"},
    ]
    out = _strip_internal_fields(msgs)
    assert out[0] == {"role": "assistant", "content": None, "tool_calls": [{"id": "a"}]}
    assert out[1] == {"role": "tool", "tool_call_id": "a", "content": "result"}


def test_does_not_mutate_input():
    msgs = [{"role": "user", "content": "hi", "source": "telegram"}]
    _strip_internal_fields(msgs)
    assert msgs[0]["source"] == "telegram"
