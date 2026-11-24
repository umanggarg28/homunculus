"""Budget and limits: what bounds a job's steps, time and cost, and the month's spend.

The loop tests use the scripted model, a fake clock and a fake reading of the month's
spend. The model tests use the real model module with only the network replaced.
"""

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from scripted_model import Reply, ScriptedModel

from homunculus import limits as limits_module
from homunculus import loop
from homunculus.activity import Activity
from homunculus.approvals import Approvals
from homunculus.limits import Budget, Limits
from homunculus.model import Message, ModelError, OpenRouterModel, Retrying, ToolCall, Usage
from homunculus.tools import Tool
from homunculus.tools.files import file_tools


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def spend_of(*amounts: float) -> tuple[Callable[[], Awaitable[float]], list[int]]:
    """A month's spend that reads as each amount in turn, and a count of the readings."""
    readings: list[int] = []
    queue = list(amounts)

    async def spend() -> float:
        readings.append(1)
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return spend, readings


async def unknown_spend() -> float:
    raise httpx.ConnectError("no route to OpenRouter")


def read_call(n: int) -> ToolCall:
    return ToolCall(id=f"call_{n}", name="list", arguments=json.dumps({"path": "."}))


async def run_job(
    model: ScriptedModel,
    tmp_path: Path,
    limits: Limits | None = None,
    budget: Budget | None = None,
    unprompted: bool = False,
    clock: Callable[[], float] | None = None,
    tools: list[Tool] | None = None,
) -> tuple[list[Any], list[Message], Activity]:
    activity = Activity(tmp_path)
    history: list[Message] = []
    items = [
        item
        async for item in loop.run(
            model,
            tools if tools is not None else file_tools(tmp_path / "workspace"),
            activity,
            Approvals(tmp_path / "approvals.json"),
            history,
            "Tidy my notes.",
            limits=limits or Limits(),
            budget=budget,
            unprompted=unprompted,
            clock=clock or Clock(),
        )
    ]
    return items, history, activity


@pytest.fixture(autouse=True)
def workspace(tmp_path: Path) -> None:
    (tmp_path / "workspace").mkdir()


def stops(items: list[Any]) -> list[loop.Stopped]:
    return [item for item in items if isinstance(item, loop.Stopped)]


# Per job


async def test_a_job_ends_when_it_reaches_its_step_limit(tmp_path: Path) -> None:
    model = ScriptedModel([[read_call(1)], [read_call(2)], [read_call(3)], ["Done."]])

    items, history, activity = await run_job(model, tmp_path, Limits(job_steps=2))

    (stop,) = stops(items)
    assert stop.limit == "steps"
    assert stop.message.startswith("I stopped: this job reached its limit of 2 steps.")
    assert len([i for i in items if isinstance(i, loop.ToolStep)]) == 2
    assert len(model.received) == 2
    assert history[-1] == {"role": "assistant", "content": stop.message, "notice": True}
    (job,) = activity.jobs()
    assert job["limit"] == "steps"
    assert job["error"] is None


async def test_a_job_ends_when_it_reaches_its_time_limit(tmp_path: Path) -> None:
    clock = Clock()

    def slow_list(arguments: dict[str, Any]) -> str:
        clock.now += 6 * 60
        return "notes.md"

    slow = file_tools(tmp_path / "workspace")[0]
    tool = Tool(slow.definition, slow_list, workspace_paths=slow.workspace_paths)
    call: Reply = [ToolCall(id="a", name=tool.name, arguments='{"path": "."}')]
    model = ScriptedModel([call, call, call, ["Done."]])

    items, _, _ = await run_job(model, tmp_path, Limits(job_minutes=10), clock=clock, tools=[tool])

    (stop,) = stops(items)
    assert stop.limit == "minutes"
    assert "10 minutes" in stop.message
    assert len(model.received) == 2


async def test_a_job_ends_once_its_cost_reaches_the_limit(tmp_path: Path) -> None:
    usage = Usage(input_tokens=1000, output_tokens=100, cost_usd=0.03)
    model = ScriptedModel([[read_call(1), usage], [read_call(2), usage], ["Done."]])

    items, _, activity = await run_job(model, tmp_path, Limits(job_cost_usd=0.05))

    (stop,) = stops(items)
    assert stop.limit == "cost"
    assert "$0.05" in stop.message
    (job,) = activity.jobs()
    assert job["cost"] == pytest.approx(0.06)


async def test_a_job_within_its_limits_is_not_stopped_and_its_cost_is_recorded(
    tmp_path: Path,
) -> None:
    model = ScriptedModel([["Done.", Usage(input_tokens=10, output_tokens=2, cost_usd=0.0004)]])

    items, history, activity = await run_job(model, tmp_path)

    assert stops(items) == []
    assert history[-1]["content"] == "Done."
    (job,) = activity.jobs()
    assert job["cost"] == pytest.approx(0.0004)
    assert job["limit"] is None


# The month


async def test_chat_is_refused_once_the_months_budget_is_spent(tmp_path: Path) -> None:
    spend, _ = spend_of(5.02)
    model = ScriptedModel([["Hello."]])

    items, history, _ = await run_job(
        model, tmp_path, budget=Budget(Limits(monthly_budget_usd=5), spend)
    )

    (stop,) = stops(items)
    assert stop.limit == "budget"
    assert "$5.00" in stop.message and "$5.02" in stop.message
    assert model.received == []
    assert history[-1]["notice"] is True


async def test_unprompted_work_stops_at_80_percent_while_chat_carries_on(tmp_path: Path) -> None:
    spend, _ = spend_of(4.20)
    budget = Budget(Limits(monthly_budget_usd=5, unprompted_stop_at=0.8), spend)

    unprompted, _, _ = await run_job(
        ScriptedModel([["Working."]]), tmp_path / "a", budget=budget, unprompted=True
    )
    chat, _, _ = await run_job(ScriptedModel([["Hello."]]), tmp_path / "b", budget=budget)

    (stop,) = stops(unprompted)
    assert stop.limit == "budget"
    assert "80%" in stop.message
    assert stops(chat) == []
    assert "Hello." in chat


async def test_when_the_spend_cannot_be_read_chat_goes_on_and_unprompted_work_does_not(
    tmp_path: Path,
) -> None:
    budget = Budget(Limits(), unknown_spend)

    unprompted, _, _ = await run_job(
        ScriptedModel([["Working."]]), tmp_path / "a", budget=budget, unprompted=True
    )
    chat, _, _ = await run_job(ScriptedModel([["Hello."]]), tmp_path / "b", budget=budget)

    (stop,) = stops(unprompted)
    assert "could not check" in stop.message
    assert stops(chat) == []


async def test_the_months_spend_is_read_at_most_once_a_minute() -> None:
    clock = Clock()
    spend, readings = spend_of(1.0, 2.0)
    budget = Budget(Limits(), spend, clock=clock)

    first = await budget.spent()
    clock.now += 30
    again = await budget.spent()
    clock.now += 31
    later = await budget.spent()

    assert (first, again, later) == (1.0, 1.0, 2.0)
    assert len(readings) == 2


def test_limits_are_written_with_their_defaults_and_your_changes_apply(tmp_path: Path) -> None:
    written = limits_module.load(tmp_path)
    text = (tmp_path / "limits.toml").read_text()

    assert written == Limits()
    assert "monthly_budget_usd = 5" in text and "job_steps = 25" in text

    (tmp_path / "limits.toml").write_text("job_steps = 40\n")

    assert limits_module.load(tmp_path) == Limits(job_steps=40)


# The model module: cost, retries and the month's spend, with only the network replaced


def chunk(data: dict[str, Any]) -> str:
    return f"data: {json.dumps(data)}"


def stream(*texts: str, cost: float = 0.0012) -> str:
    lines = [chunk({"choices": [{"delta": {"content": t}}]}) for t in texts]
    usage = {"prompt_tokens": 50, "completion_tokens": 5, "cost": cost}
    lines += [chunk({"choices": [], "usage": usage}), "data: [DONE]"]
    return "\n\n".join(lines) + "\n\n"


def network(*answers: httpx.Response | Exception) -> tuple[httpx.AsyncClient, list[httpx.Request]]:
    sent: list[httpx.Request] = []
    queue = list(answers)

    def reply(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        answer = queue.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    return httpx.AsyncClient(transport=httpx.MockTransport(reply)), sent


async def collect(model: OpenRouterModel) -> list[Any]:
    return [item async for item in model.stream([{"role": "user", "content": "Hi"}], [])]


async def no_wait(seconds: float) -> None:
    waited.append(seconds)


waited: list[float] = []


@pytest.fixture(autouse=True)
def reset_waits() -> None:
    waited.clear()


async def test_the_cost_of_each_call_is_read_from_openrouter() -> None:
    client, _ = network(httpx.Response(200, text=stream("Hello", cost=0.0012)))

    items = await collect(OpenRouterModel("key", client, sleep=no_wait))

    (usage,) = [i for i in items if isinstance(i, Usage)]
    assert usage.cost_usd == pytest.approx(0.0012)


async def test_a_failed_call_is_retried_with_a_wait_and_the_operator_sees_it() -> None:
    client, sent = network(
        httpx.Response(429, json={"error": {"message": "Rate limited"}}),
        httpx.ConnectError("connection reset"),
        httpx.Response(200, text=stream("Hello")),
    )

    items = await collect(OpenRouterModel("key", client, sleep=no_wait))

    assert [i for i in items if isinstance(i, Retrying)] == [Retrying(2, 3), Retrying(3, 3)]
    assert "Hello" in items
    assert len(sent) == 3
    assert waited == [2, 8]


async def test_after_three_failed_attempts_the_operator_is_told() -> None:
    client, sent = network(*[httpx.Response(503, text="unavailable")] * 3)

    with pytest.raises(ModelError, match="after 3 attempts"):
        await collect(OpenRouterModel("key", client, sleep=no_wait))
    assert len(sent) == 3


async def test_a_request_the_provider_refuses_is_not_retried() -> None:
    client, sent = network(httpx.Response(402, json={"error": {"message": "Insufficient credits"}}))

    with pytest.raises(ModelError, match="Insufficient credits"):
        await collect(OpenRouterModel("key", client, sleep=no_wait))
    assert len(sent) == 1


async def test_a_call_that_fails_after_words_arrived_is_not_retried() -> None:
    broken = stream("Hel").replace("data: [DONE]", 'data: {"error": {"message": "overloaded"}}')
    client, sent = network(httpx.Response(200, text=broken))

    with pytest.raises(ModelError, match="overloaded"):
        await collect(OpenRouterModel("key", client, sleep=no_wait))
    assert len(sent) == 1


async def test_the_months_spend_comes_from_the_key() -> None:
    client, sent = network(
        httpx.Response(200, json={"data": {"usage_monthly": 1.23, "limit": 10, "usage": 40}})
    )

    spent = await OpenRouterModel("key", client, sleep=no_wait).month_spend()

    assert spent == 1.23
    assert str(sent[0].url) == "https://openrouter.ai/api/v1/key"
    assert sent[0].headers["Authorization"] == "Bearer key"


async def test_a_limit_below_a_cent_is_named_as_it_is(tmp_path: Path) -> None:
    usage = Usage(input_tokens=10, output_tokens=2, cost_usd=0.0003)
    model = ScriptedModel([[read_call(1), usage], ["Done."]])

    items, _, _ = await run_job(model, tmp_path, Limits(job_cost_usd=0.0001))

    (stop,) = stops(items)
    assert "$0.0001" in stop.message
