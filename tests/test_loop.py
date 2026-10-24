from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from scripted_model import ScriptedModel

from homunculus import loop
from homunculus.activity import Activity
from homunculus.approvals import Approvals
from homunculus.model import Message, Model, ModelError, ToolCall, Usage


@pytest.fixture
def activity(tmp_path: Path) -> Activity:
    return Activity(tmp_path)


@pytest.fixture
def approvals(tmp_path: Path) -> Approvals:
    return Approvals(tmp_path / "approvals.json")


async def collect(
    model: Model, history: list[Message], text: str, activity: Activity, approvals: Approvals
) -> list[str]:
    items = [item async for item in loop.run(model, [], activity, approvals, history, text)]
    return [item for item in items if isinstance(item, str)]


async def test_streams_the_reply_and_stops_when_the_model_answers_in_words(
    activity: Activity, approvals: Approvals
) -> None:
    model = ScriptedModel([["Good ", "morning."]])

    chunks = await collect(model, [], "Hello", activity, approvals)

    assert chunks == ["Good ", "morning."]
    assert len(model.received) == 1


async def test_the_next_message_carries_the_earlier_exchange(
    activity: Activity, approvals: Approvals
) -> None:
    model = ScriptedModel([["Noted."], ["You said you like tea."]])
    history: list[Message] = []

    await collect(model, history, "I like tea.", activity, approvals)
    await collect(model, history, "What do I like?", activity, approvals)

    second_call = model.received[1]
    assert [(m["role"], m["content"]) for m in second_call[1:]] == [
        ("user", "I like tea."),
        ("assistant", "Noted."),
        ("user", "What do I like?"),
    ]


class FailingModel:
    name = "failing"
    provider = "scripted"

    async def stream(
        self, messages: list[Message], tools: list[dict[str, Any]]
    ) -> AsyncIterator[str | ToolCall | Usage]:
        yield "Good "
        raise ModelError("Model stopped mid-reply: connection lost")


async def test_a_failed_reply_leaves_the_conversation_unchanged(
    activity: Activity, approvals: Approvals
) -> None:
    history: list[Message] = []

    with pytest.raises(ModelError):
        await collect(FailingModel(), history, "Hello", activity, approvals)

    assert history == []


async def test_a_failed_reply_is_recorded_in_the_activity_log(
    activity: Activity, approvals: Approvals
) -> None:
    with pytest.raises(ModelError):
        await collect(FailingModel(), [], "Hello", activity, approvals)

    [job] = activity.jobs()
    assert (job["message"], job["reply"]) == ("Hello", None)
    assert job["error"] == "Model stopped mid-reply: connection lost"
