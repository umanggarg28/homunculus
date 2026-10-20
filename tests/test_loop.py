from collections.abc import AsyncIterator

import pytest
from scripted_model import ScriptedModel

from homunculus import loop
from homunculus.model import Message, Model, ModelError


async def collect(model: Model, history: list[Message], text: str) -> list[str]:
    return [chunk async for chunk in loop.run(model, history, text)]


async def test_streams_the_reply_and_stops_when_the_model_answers_in_words() -> None:
    model = ScriptedModel([["Good ", "morning."]])

    chunks = await collect(model, [], "Hello")

    assert chunks == ["Good ", "morning."]
    assert len(model.received) == 1


async def test_the_next_message_carries_the_earlier_exchange() -> None:
    model = ScriptedModel([["Noted."], ["You said you like tea."]])
    history: list[Message] = []

    await collect(model, history, "I like tea.")
    await collect(model, history, "What do I like?")

    second_call = model.received[1]
    assert [(m["role"], m["content"]) for m in second_call[1:]] == [
        ("user", "I like tea."),
        ("assistant", "Noted."),
        ("user", "What do I like?"),
    ]


class FailingModel:
    async def stream(self, messages: list[Message]) -> AsyncIterator[str]:
        yield "Good "
        raise ModelError("Model stopped mid-reply: connection lost")


async def test_a_failed_reply_leaves_the_conversation_unchanged() -> None:
    history: list[Message] = []

    with pytest.raises(ModelError):
        await collect(FailingModel(), history, "Hello")

    assert history == []
