"""remember() accepts the model's stable `content` dialect.

Regression: every reflection tick the model called
remember(name=..., content=...) — `content` where the schema says `body`
— failed schema validation, and burned 1–3 correction round-trips before
landing the right shape (12 of 14 remember failures in the live event
log were exactly this). memory_tools.remember now treats `content` as an
alias for `body`; an empty memory (neither given) is still refused.
"""

import pytest

from tests.conftest import load_real_tool_submodule

memory_tools = load_real_tool_submodule("memory_tools")


@pytest.fixture()
def captured(monkeypatch):
    calls: list[dict] = []

    class _FakeMemory:
        def remember(self, **kwargs):
            calls.append(kwargs)
            return "Saved"

    monkeypatch.setattr(memory_tools, "get_memory", lambda: _FakeMemory())
    return calls


def test_content_is_accepted_as_body_alias(captured):
    out = memory_tools.remember(
        name="n", description="d", type="feedback", content="the fact",
    )
    assert out == "Saved"
    assert captured[0]["body"] == "the fact"


def test_body_wins_when_both_are_given(captured):
    memory_tools.remember(
        name="n", description="d", type="feedback",
        body="canonical", content="ignored",
    )
    assert captured[0]["body"] == "canonical"


def test_empty_memory_is_refused(captured):
    out = memory_tools.remember(name="n", description="d", type="feedback")
    assert out.startswith("ERROR")
    assert captured == []
