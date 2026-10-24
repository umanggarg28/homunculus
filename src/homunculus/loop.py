"""The agent loop: ask the model, run the tools it asks for, repeat until it answers in words."""

import asyncio
import json
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import mcp_types as mcp
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.trace import Span, SpanKind

from homunculus import gate, prompt
from homunculus.activity import (
    AGENT,
    ROOT_SPAN,
    TURN_START,
    Activity,
    fail,
    input_messages,
    output_messages,
    traceparent_of,
)
from homunculus.approvals import Approvals, Kind
from homunculus.model import Message, Model, ModelError, ToolCall, Usage
from homunculus.tools import Tool, make_result, result_text


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
    activity: Activity,
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
    system: Message = {"role": "system", "content": prompt.system_prompt()}
    opening: Message = {"role": "user", "content": text}
    if notice:
        opening["notice"] = True
    turn: list[Message] = [opening]
    # The whole turn is one trace. Each span below names its parent explicitly, because
    # the "current span" of a context does not survive the yields of a generator.
    root = activity.tracer.start_span(
        ROOT_SPAN,
        attributes={
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.name": AGENT,
            "gen_ai.provider.name": model.provider,
            "gen_ai.request.model": model.name,
            "gen_ai.conversation.id": activity.conversation_id,
            "gen_ai.input.messages": input_messages(text),
            "homunculus.notice": notice,
        },
    )
    parent = trace.set_span_in_context(root)
    # Written as the turn begins, so the activity screen can show what was asked while the
    # work is still going on, or after it was cut off.
    activity.event(
        TURN_START,
        parent,
        {"gen_ai.input.messages": input_messages(text), "homunculus.notice": notice},
    )
    try:
        while True:
            parts: list[str] = []
            calls: list[ToolCall] = []
            messages = [system, *history, *turn]
            with _model_call(activity, model, parent) as chat:
                async for item in model.stream(messages, schemas):
                    if isinstance(item, ToolCall):
                        calls.append(item)
                    elif isinstance(item, Usage):
                        _record_usage(chat, item)
                    else:
                        parts.append(item)
                        yield item
                chat.set_attribute("gen_ai.output.messages", output_messages("".join(parts), calls))
            reply: Message = {"role": "assistant", "content": "".join(parts)}
            if calls:
                reply["tool_calls"] = [_as_message(call) for call in calls]
            turn.append(reply)
            if not calls:
                break
            for call in calls:
                step = _carry_out(call, by_name, activity, approvals, parent)
                turn.append({"role": "tool", "tool_call_id": call.id, "content": step.result})
                yield step
    except ModelError as error:
        fail(root, error, str(error))
        raise
    except BaseException as error:
        # Anything else that ends the turn early, such as a bug or the operator closing
        # the page, still ends the job, so the activity screen never shows it as running.
        fail(root, error, _stopped(error))
        raise
    root.set_attribute("gen_ai.output.messages", output_messages(reply["content"]))
    root.end()
    history += turn


def _carry_out(
    call: ToolCall,
    by_name: dict[str, Tool],
    activity: Activity,
    approvals: Approvals,
    parent: Context,
) -> ToolStep:
    """Put one tool call through the gate and, if allowed, run it. The step's result is
    what the model sees."""
    started = time.monotonic()
    span = activity.start_tool(call.name, call.arguments, parent, call_id=call.id)
    try:
        answer, result, outcome, request_id = _decide_and_run(call, by_name, approvals, parent)
    except BaseException as error:
        fail(span, error, str(error) or type(error).__name__)
        raise
    activity.end_tool(span, answer.value, result, outcome, request_id)
    return ToolStep(
        name=call.name,
        arguments=call.arguments,
        answer=answer.value,
        result=result,
        duration_ms=round((time.monotonic() - started) * 1000),
        outcome=outcome,
    )


def _decide_and_run(
    call: ToolCall, by_name: dict[str, Tool], approvals: Approvals, parent: Context
) -> tuple[gate.Answer, str, str, str | None]:
    """The gate's answer, the result the model sees, the outcome, and the approval
    request made, if any."""
    answer = gate.decide(call, by_name.get(call.name))
    request_id = None
    if answer is gate.Answer.ACT:
        done = run_tool(call.name, call.arguments, by_name)
        result, outcome = as_text(done), outcome_of(done)
    elif answer in WAITS_FOR_OPERATOR:
        outcome = "waiting"
        request = approvals.request(
            call.name,
            call.arguments,
            WAITS_FOR_OPERATOR[answer],
            traceparent=traceparent_of(parent),
            tool_call_id=call.id,
        )
        request_id = request.id
        result = (
            f"Waiting for the operator's approval (request {request.id}). Carry on with other "
            "work; you will be told the outcome."
        )
    elif call.name not in by_name:
        # Said plainly, so the model can correct the name instead of retrying.
        result = f"Error: there is no tool named {call.name}."
        outcome = "failed"
    else:
        result = f"Not done: the gate answered '{answer.value}' for this action."
        outcome = "failed"
    return answer, result, outcome, request_id


# Answers that wait for the operator, and the kind of request each one creates. An
# action allowed only when the operator asked for it becomes a request as well,
# because the loop does not track which actions the operator asked for.
WAITS_FOR_OPERATOR: dict[gate.Answer, Kind] = {
    gate.Answer.ASK: "ask",
    gate.Answer.ACT_WHEN_ASKED: "ask",
    gate.Answer.HAND_OFF: "hand off",
}


def run_tool(name: str, arguments: str, by_name: dict[str, Tool]) -> mcp.CallToolResult:
    """Run one tool and return its result. Failures are results with `is_error` set,
    never raised."""
    try:
        if name not in by_name:
            return make_result(f"there is no tool named {name}.", is_error=True)
        return by_name[name].call(json.loads(arguments))
    except Exception as error:
        # A bug or bad input inside a tool must not end the whole reply. The model is
        # told, the step is recorded, and the loop carries on.
        detail = f"{name} failed unexpectedly ({type(error).__name__}: {error})."
        return make_result(detail, is_error=True)


def as_text(result: mcp.CallToolResult) -> str:
    """What the model is told. Chat Completions has no error flag, so a failure is
    marked in the text itself."""
    text = result_text(result)
    return f"Error: {text}" if result.is_error else text


def outcome_of(result: mcp.CallToolResult) -> str:
    """Whether a tool that ran succeeded: "ok", or "failed" when it reported an error."""
    return "failed" if result.is_error else "ok"


@contextmanager
def _model_call(activity: Activity, model: Model, parent: Context) -> Iterator[Span]:
    """The span of one model call, ended when the call is, with an error status if it
    failed."""
    span = activity.tracer.start_span(
        f"chat {model.name}",
        context=parent,
        kind=SpanKind.CLIENT,
        attributes={
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": model.provider,
            "gen_ai.request.model": model.name,
        },
    )
    try:
        yield span
    except BaseException as error:
        fail(span, error, str(error) or type(error).__name__)
        raise
    span.end()


def _record_usage(span: Span, usage: Usage) -> None:
    span.set_attribute("gen_ai.usage.input_tokens", usage.input_tokens)
    span.set_attribute("gen_ai.usage.output_tokens", usage.output_tokens)
    if usage.cached_input_tokens:
        span.set_attribute("gen_ai.usage.cache_read.input_tokens", usage.cached_input_tokens)


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
