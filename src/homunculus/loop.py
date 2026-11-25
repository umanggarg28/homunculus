"""The agent loop: ask the model, run the tools it asks for, repeat until it answers in words."""

import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from zoneinfo import ZoneInfo

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
from homunculus.limits import Budget, Limits, dollars
from homunculus.memory import Memory
from homunculus.model import Message, Model, ModelError, Retrying, ToolCall, Usage
from homunculus.skills import Skills
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


def local_time(moment: datetime) -> str:
    """The date and time in words, with the time zone, as the agent is told it."""
    zone = moment.tzinfo
    name = zone.key if isinstance(zone, ZoneInfo) else moment.strftime("%Z")
    return f"It is {moment.strftime('%A %-d %B %Y, %H:%M')} ({name})."


def _local_now() -> datetime:
    return datetime.now().astimezone()


class OwnFiles(Protocol):
    """The workspace files the agent made on its own, which it may change again."""

    def __contains__(self, path: object) -> bool: ...

    def add(self, path: str) -> None: ...


@dataclass(frozen=True)
class Stopped:
    """A limit ended the job, or kept it from starting: which one, and what the operator
    is told."""

    limit: str  # "steps", "minutes", "cost", "budget", "operator" or "paused"
    message: str


async def run(
    model: Model,
    tools: list[Tool],
    activity: Activity,
    approvals: Approvals,
    history: list[Message],
    text: str,
    notice: bool = False,
    memory: Memory | None = None,
    limits: Limits | None = None,
    budget: Budget | None = None,
    unprompted: bool = False,
    clock: Callable[[], float] = time.monotonic,
    interruption: Callable[[], str | None] | None = None,
    wake_reason: str | None = None,
    own_files: OwnFiles | None = None,
    skills: Skills | None = None,
    now: Callable[[], datetime] | None = None,
) -> AsyncIterator[str | ToolStep | Retrying | Stopped]:
    """Answer one operator message, yielding the reply text as it streams and a
    ToolStep after each tool call, so a caller can show the work as it happens.

    `history` holds the earlier turns. The new turn, including any tool calls and
    their results, is added to it only once the final reply is complete, so a
    failed call leaves the conversation unchanged.

    A `notice` is a message the program writes on the operator's behalf, such as the
    outcome of an approval. It is marked so the console can show it as one.

    With `memory`, the turn starts by taking any edits the operator made by hand, and the
    instructions are followed by the profile, goals and wiki index, read fresh each turn.
    With `skills`, the catalogue of skills follows them, also built fresh each turn.
    The local date and time, from `now`, come last.

    Every job is bounded by `limits` (the defaults when none are given): it ends once it
    reaches its steps, minutes or cost, and with a `budget` it does not start once the
    month's budget is spent, or for `unprompted` work, its share of it. A Stopped says
    which, and its message is added to the conversation as a notice.

    `interruption` is asked at each step boundary whether the job should give way:
    "operator" when the operator's message is waiting, "paused" when they paused
    unprompted work. An `unprompted` job records why it woke, as `wake_reason`, and
    `own_files` holds the files it made on its own, which it may change again.
    """
    limits = limits or Limits()
    began = clock()
    steps = 0
    cost = 0.0
    by_name = {tool.name: tool for tool in tools}
    schemas = [tool.schema() for tool in tools]
    instructions = prompt.system_prompt()
    if memory is not None:
        memory.take_hand_edits()
        instructions += "\n\n" + memory.context()
    catalogue = skills.catalogue() if skills is not None else ""
    if catalogue:
        instructions += "\n\n" + catalogue
    # Last, so everything before it is the same from turn to turn and a provider can
    # cache it.
    instructions += "\n\n" + local_time((now or _local_now)())
    system: Message = {"role": "system", "content": instructions}
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
            "homunculus.unprompted": unprompted,
            **({"homunculus.wake_reason": wake_reason} if wake_reason else {}),
        },
    )
    parent = trace.set_span_in_context(root)
    # Written as the turn begins, so the activity screen can show what was asked while the
    # work is still going on, or after it was cut off.
    activity.event(
        TURN_START,
        parent,
        {
            "gen_ai.input.messages": input_messages(text),
            "homunculus.notice": notice,
            "homunculus.unprompted": unprompted,
            **({"homunculus.wake_reason": wake_reason} if wake_reason else {}),
        },
    )
    try:
        stop = None
        refusal = await budget.refusal(unprompted) if budget is not None else None
        if refusal is not None:
            stop = Stopped("budget", refusal)
        while stop is None:
            parts: list[str] = []
            calls: list[ToolCall] = []
            messages = [system, *history, *turn]
            with _model_call(activity, model, parent) as chat:
                async for item in model.stream(messages, schemas):
                    if isinstance(item, ToolCall):
                        calls.append(item)
                    elif isinstance(item, Usage):
                        _record_usage(chat, item)
                        cost += item.cost_usd or 0
                    elif isinstance(item, Retrying):
                        yield item
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
                if steps >= limits.job_steps:
                    # Every call the model made gets an answer, so the conversation stays
                    # well formed for the next turn.
                    skipped = "Not run: this job reached its step limit."
                    turn.append({"role": "tool", "tool_call_id": call.id, "content": skipped})
                    continue
                step = await _carry_out(
                    call, by_name, activity, approvals, parent, unprompted, own_files
                )
                steps += 1
                turn.append({"role": "tool", "tool_call_id": call.id, "content": step.result})
                yield step
            asked = interruption() if interruption is not None else None
            stop = INTERRUPTED[asked] if asked in INTERRUPTED else None
            stop = stop or _over(limits, steps, (clock() - began) / 60, cost)
        if stop is not None:
            reply = {"role": "assistant", "content": stop.message, "notice": True}
            turn.append(reply)
            root.set_attribute("homunculus.limit", stop.limit)
            yield stop
    except ModelError as error:
        fail(root, error, str(error))
        raise
    except BaseException as error:
        # Anything else that ends the turn early, such as a bug or the operator pressing
        # stop, still ends the job, so the activity screen never shows it as running.
        fail(root, error, stopped_message(error))
        raise
    root.set_attribute("gen_ai.output.messages", output_messages(reply["content"]))
    root.end()
    history += turn


async def _carry_out(
    call: ToolCall,
    by_name: dict[str, Tool],
    activity: Activity,
    approvals: Approvals,
    parent: Context,
    unprompted: bool = False,
    own_files: "OwnFiles | None" = None,
) -> ToolStep:
    """Put one tool call through the gate and, if allowed, run it. The step's result is
    what the model sees."""
    started = time.monotonic()
    span = activity.start_tool(call.name, call.arguments, parent, call_id=call.id)
    try:
        answer, result, outcome, request_id = await _decide_and_run(
            call, by_name, approvals, parent, unprompted, own_files
        )
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


async def _decide_and_run(
    call: ToolCall,
    by_name: dict[str, Tool],
    approvals: Approvals,
    parent: Context,
    unprompted: bool = False,
    own_files: "OwnFiles | None" = None,
) -> tuple[gate.Answer, str, str, str | None]:
    """The gate's answer, the result the model sees, the outcome, and the approval
    request made, if any."""
    tool = by_name.get(call.name)
    own: OwnFiles | frozenset[str] = own_files if own_files is not None else frozenset()
    answer = gate.decide(call, tool, unprompted=unprompted, own=own)
    request_id = None
    made: list[str] = []
    if unprompted and own_files is not None and tool is not None and tool.changes:
        # Worked out before the call runs, while a new file is still new.
        try:
            _, made = tool.changes(json.loads(call.arguments))
        except Exception:
            made = []
    if answer is gate.Answer.ACT:
        # In a worker thread, so a slow command does not stop the server answering anyone
        # else, such as a page asking to stop this very turn.
        done = await asyncio.to_thread(run_tool, call.name, call.arguments, by_name)
        result, outcome = as_text(done), outcome_of(done)
        if outcome == "ok" and own_files is not None:
            for path in made:
                own_files.add(path)
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


CARRY_ON = "Ask me to carry on if you want more."
NOT_UNDONE = "Steps that already ran were not undone."
# Why an unprompted job gives way at a step boundary, and what it says.
INTERRUPTED = {
    "operator": Stopped("operator", f"Stopped for your message. {NOT_UNDONE}"),
    "paused": Stopped("paused", f"Paused by you. {NOT_UNDONE}"),
}


def _steps(n: int) -> str:
    return "1 step" if n == 1 else f"{n} steps"


def _over(limits: Limits, steps: int, minutes: float, cost: float) -> Stopped | None:
    """The limit a job has reached, if any. Checked after each round of tool calls, so a
    call already running finishes first."""
    if steps >= limits.job_steps:
        return Stopped(
            "steps",
            f"I stopped: this job reached its limit of {_steps(limits.job_steps)}. {CARRY_ON}",
        )
    if minutes >= limits.job_minutes:
        return Stopped(
            "minutes",
            f"I stopped: this job reached its limit of {limits.job_minutes:g} minutes. {CARRY_ON}",
        )
    if cost >= limits.job_cost_usd:
        return Stopped(
            "cost",
            f"I stopped: this job reached its cost limit of {dollars(limits.job_cost_usd)}. "
            + CARRY_ON,
        )
    return None


def _record_usage(span: Span, usage: Usage) -> None:
    span.set_attribute("gen_ai.usage.input_tokens", usage.input_tokens)
    span.set_attribute("gen_ai.usage.output_tokens", usage.output_tokens)
    if usage.cached_input_tokens:
        span.set_attribute("gen_ai.usage.cache_read.input_tokens", usage.cached_input_tokens)
    # The GenAI conventions name no cost attribute, so this one is the program's own.
    if usage.cost_usd is not None:
        span.set_attribute("homunculus.cost_usd", usage.cost_usd)


# The reason given when the operator stops a turn, so its record can say who stopped it.
STOPPED_BY_OPERATOR = "stopped by the operator"


def stopped_message(error: BaseException) -> str:
    """Why a turn ended early, in words for the operator. A cancellation's own text names
    internal tasks, which mean nothing to the operator."""
    if isinstance(error, asyncio.CancelledError) and error.args == (STOPPED_BY_OPERATOR,):
        return "The turn was stopped by the operator."
    if isinstance(error, asyncio.CancelledError | GeneratorExit):
        return "The turn stopped before it finished: it was cancelled."
    detail = f"{type(error).__name__}: {error}" if str(error) else type(error).__name__
    return f"The turn stopped before it finished ({detail})."


def _as_message(call: ToolCall) -> dict[str, object]:
    return {
        "id": call.id,
        "type": "function",
        "function": {"name": call.name, "arguments": call.arguments},
    }
