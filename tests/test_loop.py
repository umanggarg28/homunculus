from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from scripted_model import ScriptedModel

from homunculus import loop
from homunculus.activity import ActivityLog
from homunculus.model import Message, Model, ModelError, ToolCall


@pytest.fixture
def activity(tmp_path: Path) -> ActivityLog:
    return ActivityLog(tmp_path / "activity.jsonl")


async def collect(
    model: Model, history: list[Message], text: str, activity: ActivityLog
) -> list[str]:
    return [chunk async for chunk in loop.run(model, [], activity, history, text)]


async def test_streams_the_reply_and_stops_when_the_model_answers_in_words(
    activity: ActivityLog,
) -> None:
    model = ScriptedModel([["Good ", "morning."]])

    chunks = await collect(model, [], "Hello", activity)

    assert chunks == ["Good ", "morning."]
    assert len(model.received) == 1


async def test_the_next_message_carries_the_earlier_exchange(activity: ActivityLog) -> None:
    model = ScriptedModel([["Noted."], ["You said you like tea."]])
    history: list[Message] = []

    await collect(model, history, "I like tea.", activity)
    await collect(model, history, "What do I like?", activity)

    second_call = model.received[1]
    assert [(m["role"], m["content"]) for m in second_call[1:]] == [
        ("user", "I like tea."),
        ("assistant", "Noted."),
        ("user", "What do I like?"),
    ]


class FailingModel:
    async def stream(
        self, messages: list[Message], tools: list[dict[str, Any]]
    ) -> AsyncIterator[str | ToolCall]:
        yield "Good "
        raise ModelError("Model stopped mid-reply: connection lost")


async def test_a_failed_reply_leaves_the_conversation_unchanged(activity: ActivityLog) -> None:
    history: list[Message] = []

    with pytest.raises(ModelError):
        await collect(FailingModel(), history, "Hello", activity)

    assert history == []


async def test_a_failed_reply_is_recorded_in_the_activity_log(activity: ActivityLog) -> None:
    with pytest.raises(ModelError):
        await collect(FailingModel(), [], "Hello", activity)

    assert [(e["kind"], e.get("error")) for e in activity.entries()] == [
        ("message", None),
        ("error", "Model stopped mid-reply: connection lost"),
    ]
