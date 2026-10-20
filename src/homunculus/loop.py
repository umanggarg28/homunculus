"""The agent loop: ask the model, run the tools it asks for, repeat until it answers in words."""

import json
from collections.abc import AsyncIterator

from homunculus import gate
from homunculus.activity import ActivityLog
from homunculus.model import Message, Model, ModelError, ToolCall
from homunculus.tools import Tool, ToolError

SYSTEM_PROMPT = "You are Homunculus, a personal agent working on behalf of your operator."


async def run(
    model: Model,
    tools: list[Tool],
    activity: ActivityLog,
    history: list[Message],
    text: str,
) -> AsyncIterator[str]:
    """Answer one operator message, yielding the reply text as it streams.

    `history` holds the earlier turns. The new turn, including any tool calls and
    their results, is added to it only once the final reply is complete, so a
    failed call leaves the conversation unchanged.
    """
    by_name = {tool.name: tool for tool in tools}
    schemas = [tool.schema() for tool in tools]
    turn: list[Message] = [{"role": "user", "content": text}]
    activity.record("message", text=text)
    try:
        while True:
            parts: list[str] = []
            calls: list[ToolCall] = []
            messages = [{"role": "system", "content": SYSTEM_PROMPT}, *history, *turn]
            async for item in model.stream(messages, schemas):
                if isinstance(item, ToolCall):
                    calls.append(item)
                else:
                    parts.append(item)
                    yield item
            reply: Message = {"role": "assistant", "content": "".join(parts)}
            if calls:
                reply["tool_calls"] = [_as_message(call) for call in calls]
            turn.append(reply)
            if not calls:
                break
            for call in calls:
                result = _carry_out(call, by_name, activity)
                turn.append({"role": "tool", "tool_call_id": call.id, "content": result})
    except ModelError as error:
        activity.record("error", error=str(error))
        raise
    activity.record("reply", text=reply["content"])
    history += turn


def _carry_out(call: ToolCall, by_name: dict[str, Tool], activity: ActivityLog) -> str:
    """Put one tool call through the gate and, if allowed, run it. Returns what the model sees."""
    answer = gate.decide(call)
    if answer is gate.Answer.ACT:
        try:
            if call.name not in by_name:
                raise ToolError(f"there is no tool named {call.name}.")
            result = by_name[call.name].call(json.loads(call.arguments))
        except ToolError as error:
            result = f"Error: {error}"
        except Exception as error:
            # A bug or bad input inside a tool must not end the whole reply. The model is
            # told, the step is recorded, and the loop carries on.
            result = f"Error: {call.name} failed unexpectedly ({type(error).__name__}: {error})."
    else:
        result = f"Not done: the gate answered '{answer.value}' for this action."
    activity.record(
        "tool", name=call.name, arguments=call.arguments, answer=answer.value, result=result
    )
    return result


def _as_message(call: ToolCall) -> dict[str, object]:
    return {
        "id": call.id,
        "type": "function",
        "function": {"name": call.name, "arguments": call.arguments},
    }
