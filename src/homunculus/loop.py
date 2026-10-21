"""The agent loop: ask the model, run the tools it asks for, repeat until it answers in words."""

import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

from homunculus import gate
from homunculus.activity import ActivityLog
from homunculus.model import Message, Model, ModelError, ToolCall
from homunculus.tools import Tool, ToolError

SYSTEM_PROMPT = "You are Homunculus, a personal agent working on behalf of your operator."


@dataclass(frozen=True)
class ToolStep:
    """One tool call the loop has dealt with: what was asked, the gate's answer, the result,
    and how long it took."""

    name: str
    arguments: str
    answer: str
    result: str
    duration_ms: int


async def run(
    model: Model,
    tools: list[Tool],
    activity: ActivityLog,
    history: list[Message],
    text: str,
) -> AsyncIterator[str | ToolStep]:
    """Answer one operator message, yielding the reply text as it streams and a
    ToolStep after each tool call, so a caller can show the work as it happens.

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
                step = _carry_out(call, by_name, activity)
                turn.append({"role": "tool", "tool_call_id": call.id, "content": step.result})
                yield step
    except ModelError as error:
        activity.record("error", error=str(error))
        raise
    activity.record("reply", text=reply["content"])
    history += turn


def _carry_out(call: ToolCall, by_name: dict[str, Tool], activity: ActivityLog) -> ToolStep:
    """Put one tool call through the gate and, if allowed, run it. The step's result is
    what the model sees."""
    started = time.monotonic()
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
    return ToolStep(
        name=call.name,
        arguments=call.arguments,
        answer=answer.value,
        result=result,
        duration_ms=round((time.monotonic() - started) * 1000),
    )


def _as_message(call: ToolCall) -> dict[str, object]:
    return {
        "id": call.id,
        "type": "function",
        "function": {"name": call.name, "arguments": call.arguments},
    }
