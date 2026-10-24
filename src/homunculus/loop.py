"""The agent loop: ask the model, run the tools it asks for, repeat until it answers in words."""

import asyncio
import json
import secrets
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

from homunculus import gate
from homunculus.activity import ActivityLog
from homunculus.approvals import Approvals, Kind
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
    # How the step ended, so every reader classifies it the same way: "ok" (it ran),
    # "waiting" (the operator decides) or "failed" (refused, or the tool reported an error).
    outcome: str


async def run(
    model: Model,
    tools: list[Tool],
    activity: ActivityLog,
    approvals: Approvals,
    history: list[Message],
    text: str,
    notice: bool = False,
) -> AsyncIterator[str | ToolStep]:
    """Answer one operator message, yielding the reply text as it streams and a
    ToolStep after each tool call, so a caller can show the work as it happens.

    `history` holds the earlier turns. The new turn, including any tool calls and
    their results, is added to it only once the final reply is complete, so a
    failed call leaves the conversation unchanged.

    A `notice` is a message the program writes on the operator's behalf, such as the
    outcome of an approval. It is marked so the console can show it as one.
    """
    by_name = {tool.name: tool for tool in tools}
    schemas = [tool.schema() for tool in tools]
    # Every line this turn writes to the activity log carries the same job id, so the
    # activity screen can show the turn as one piece of work.
    job = secrets.token_hex(8)
    opening: Message = {"role": "user", "content": text}
    if notice:
        opening["notice"] = True
    turn: list[Message] = [opening]
    activity.record("message", job=job, text=text, notice=notice)
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
                step = _carry_out(call, by_name, activity, approvals, job)
                turn.append({"role": "tool", "tool_call_id": call.id, "content": step.result})
                yield step
    except ModelError as error:
        activity.record("error", job=job, error=str(error))
        raise
    except BaseException as error:
        # Anything else that ends the turn early, such as a bug or the operator closing
        # the page, still ends the job, so the activity screen never shows it as running.
        activity.record("error", job=job, error=_stopped(error))
        raise
    activity.record("reply", job=job, text=reply["content"])
    history += turn


def _carry_out(
    call: ToolCall,
    by_name: dict[str, Tool],
    activity: ActivityLog,
    approvals: Approvals,
    job: str,
) -> ToolStep:
    """Put one tool call through the gate and, if allowed, run it. The step's result is
    what the model sees."""
    started = time.monotonic()
    answer = gate.decide(call)
    request_id = None
    if answer is gate.Answer.ACT:
        result = run_tool(call.name, call.arguments, by_name)
        outcome = outcome_of(result)
    elif answer in WAITS_FOR_OPERATOR:
        outcome = "waiting"
        request = approvals.request(call.name, call.arguments, WAITS_FOR_OPERATOR[answer], job)
        request_id = request.id
        result = (
            f"Waiting for the operator's approval (request {request.id}). Carry on with other "
            "work; you will be told the outcome."
        )
    else:
        result = f"Not done: the gate answered '{answer.value}' for this action."
        outcome = "failed"
    activity.record(
        "tool",
        job=job,
        name=call.name,
        arguments=call.arguments,
        answer=answer.value,
        result=result,
        outcome=outcome,
        request=request_id,
    )
    return ToolStep(
        name=call.name,
        arguments=call.arguments,
        answer=answer.value,
        result=result,
        duration_ms=round((time.monotonic() - started) * 1000),
        outcome=outcome,
    )


# Answers that wait for the operator, and the kind of request each one creates. An
# action allowed only when the operator asked for it becomes a request as well,
# because the loop does not track which actions the operator asked for.
WAITS_FOR_OPERATOR: dict[gate.Answer, Kind] = {
    gate.Answer.ASK: "ask",
    gate.Answer.ACT_WHEN_ASKED: "ask",
    gate.Answer.HAND_OFF: "hand off",
}


def run_tool(name: str, arguments: str, by_name: dict[str, Tool]) -> str:
    """Run one tool and return what it reports. Failures are returned as text, never raised."""
    try:
        if name not in by_name:
            raise ToolError(f"there is no tool named {name}.")
        return by_name[name].call(json.loads(arguments))
    except ToolError as error:
        return f"Error: {error}"
    except Exception as error:
        # A bug or bad input inside a tool must not end the whole reply. The model is
        # told, the step is recorded, and the loop carries on.
        return f"Error: {name} failed unexpectedly ({type(error).__name__}: {error})."


def outcome_of(result: str) -> str:
    """Whether a tool that ran succeeded: "ok", or "failed" when it reported an error."""
    return "failed" if result.startswith("Error:") else "ok"


def _stopped(error: BaseException) -> str:
    # A closed page cancels the turn. The cancellation's own text names internal tasks,
    # which mean nothing to the operator.
    if isinstance(error, asyncio.CancelledError | GeneratorExit):
        return "The turn stopped before it finished: the connection closed."
    detail = f"{type(error).__name__}: {error}" if str(error) else type(error).__name__
    return f"The turn stopped before it finished ({detail})."


def _as_message(call: ToolCall) -> dict[str, object]:
    return {
        "id": call.id,
        "type": "function",
        "function": {"name": call.name, "arguments": call.arguments},
    }
