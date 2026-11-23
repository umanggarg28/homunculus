"""What bounds the agent's spending and its runaway jobs.

Per job: steps, minutes and cost, checked by the loop between steps. Per month: the
spend on the API key, as OpenRouter counts it. Unprompted work stops at a share of the
monthly budget, and chat at all of it. The key's own limit, set on OpenRouter, is the
backstop for a bug here.

Every number is in `limits.toml` in the data directory, written with the defaults on
first start, so the operator can see and change each one.
"""

import dataclasses
import time
import tomllib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FILE = "limits.toml"
# How long a reading of the month's spend is trusted before it is asked for again.
SPEND_FRESH_SECONDS = 60


def dollars(usd: float) -> str:
    """An amount as the operator reads it: "$5.00", and below a cent as much as it takes
    to show it ("$0.0001"), never a misleading "$0.00"."""
    return f"${usd:.2f}" if usd == 0 or usd >= 0.01 else f"${usd:.2g}"


@dataclass(frozen=True)
class Limits:
    monthly_budget_usd: float = 5.0
    # Share of the monthly budget at which unprompted work stops; chat goes on to 100%.
    unprompted_stop_at: float = 0.8
    job_steps: int = 25
    job_minutes: float = 10
    job_cost_usd: float = 0.05
    # Used by the waker.
    wakeups_per_day: int = 24


TEMPLATE = """\
# What bounds the agent's spending and its runaway jobs. Change any number; a setting
# you delete goes back to its default. Read when the agent starts.

# The month's spend is what OpenRouter counts for your API key, in US dollars.
monthly_budget_usd = {monthly_budget_usd:g}
# At this share of the budget unprompted work stops and you are told. Chat goes on
# until the whole budget is spent.
unprompted_stop_at = {unprompted_stop_at:g}

# Each job, whether you asked for it or not, ends when it reaches one of these, and
# says which. A step is one tool call.
job_steps = {job_steps}
job_minutes = {job_minutes:g}
job_cost_usd = {job_cost_usd:g}

# How many times a day the agent may wake itself to work.
wakeups_per_day = {wakeups_per_day}
"""


def load(home: Path) -> Limits:
    """The limits, written out with the defaults on first use."""
    path = home / FILE
    if not path.exists():
        path.write_text(TEMPLATE.format(**dataclasses.asdict(Limits())), encoding="utf-8")
    data: dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8"))
    changed = {name: value for name, value in data.items() if name in Limits.__dataclass_fields__}
    return dataclasses.replace(Limits(), **changed)


class Budget:
    """The month's budget, checked before each job starts."""

    def __init__(
        self,
        limits: Limits,
        spend: Callable[[], Awaitable[float]],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limits = limits
        self._spend = spend
        self._clock = clock
        self._reading: tuple[float, float] | None = None  # (when, amount)

    async def spent(self) -> float:
        """This month's spend, read again only once the last reading is a minute old."""
        now = self._clock()
        if self._reading is None or now - self._reading[0] >= SPEND_FRESH_SECONDS:
            self._reading = (now, await self._spend())
        return self._reading[1]

    async def refusal(self, unprompted: bool) -> str | None:
        """Why a job may not start, in words for the operator, or None if it may."""
        budget = self.limits.monthly_budget_usd
        try:
            spent = await self.spent()
        except Exception:
            # Unprompted work runs while the operator is away, so it fails safe. Chat
            # goes on: the operator is there, and the key's own limit still holds.
            if unprompted:
                return "I won't start unprompted work: I could not check this month's spend."
            return None
        if spent >= budget:
            return (
                f"I can't start: this month's budget of {dollars(budget)} is spent "
                f"({dollars(spent)} so far). Raise monthly_budget_usd in limits.toml to go on."
            )
        if unprompted and spent >= budget * self.limits.unprompted_stop_at:
            share = round(self.limits.unprompted_stop_at * 100)
            return (
                f"I won't start unprompted work: this month's spend ({dollars(spent)}) has "
                f"reached {share}% of the {dollars(budget)} budget. Chat still works until the "
                "budget is spent."
            )
        return None
