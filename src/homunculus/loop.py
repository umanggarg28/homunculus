"""The agent loop: send the conversation to the model and stream back its reply."""

from collections.abc import AsyncIterator

from homunculus.model import Message, Model

SYSTEM_PROMPT = "You are Homunculus, a personal agent working on behalf of your operator."


async def run(model: Model, history: list[Message], text: str) -> AsyncIterator[str]:
    """Answer one operator message, yielding the reply as it streams.

    `history` holds the earlier turns. The new exchange is added to it only once
    the reply is complete, so a failed call leaves the conversation unchanged.
    """
    user = {"role": "user", "content": text}
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, *history, user]
    parts: list[str] = []
    async for chunk in model.stream(messages):
        parts.append(chunk)
        yield chunk
    history += [user, {"role": "assistant", "content": "".join(parts)}]
