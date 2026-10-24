"""Turns that run on the server, apart from any page watching them.

A turn is a server-side task. Everything it does becomes a numbered event that is kept,
so a page can watch from the start, or come back after a reload and catch up from the
last event it saw, as server-sent events allow with `Last-Event-ID`. A page closing only
stops its watching; stopping the work is a request of its own. OpenAI's background
responses, LangGraph's background runs and the Vercel AI SDK's resumable streams work
the same way.

Events are kept in memory until the program stops. Keeping them across restarts belongs
with the saved conversation (#30).
"""

import asyncio
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from homunculus.loop import STOPPED_BY_OPERATOR, stopped_message

# How many finished turns stay available to rejoin. Older ones are let go.
KEPT_TURNS = 20


@dataclass(frozen=True)
class Event:
    id: int  # 1 for a turn's first event, then counting up
    name: str
    data: dict[str, Any]


@dataclass
class Turn:
    id: str
    events: list[Event] = field(default_factory=list)
    done: bool = False
    task: "asyncio.Task[None] | None" = None
    _changed: asyncio.Condition = field(default_factory=asyncio.Condition)

    async def add(self, name: str, data: dict[str, Any]) -> None:
        async with self._changed:
            self.events.append(Event(len(self.events) + 1, name, data))
            self._changed.notify_all()

    async def finish(self) -> None:
        async with self._changed:
            self.done = True
            self._changed.notify_all()

    async def follow(self, after: int = 0) -> AsyncIterator[Event]:
        """Every event after the one numbered `after`, then each new one as it happens,
        until the turn is done."""
        seen = after
        while True:

            def ready(seen: int = seen) -> bool:
                return len(self.events) > seen or self.done

            async with self._changed:
                await self._changed.wait_for(ready)
                new = self.events[seen:]
                finished = self.done
            for event in new:
                yield event
            seen += len(new)
            if finished and seen >= len(self.events):
                return

    def stop(self) -> None:
        """Stop the turn wherever it is, even while it waits for an earlier one."""
        if self.task is not None:
            self.task.cancel(STOPPED_BY_OPERATOR)


# What a turn does, given the turn to add its events to.
Work = Callable[[Turn], Awaitable[None]]


class Turns:
    def __init__(self) -> None:
        self._turns: dict[str, Turn] = {}
        # Tasks that close turns stopped before they began, held so they are not lost.
        self._closing: set[asyncio.Task[None]] = set()

    def start(self, work: Work) -> Turn:
        """Start `work` as a task of its own, so it carries on whoever is watching."""
        turn = Turn(secrets.token_hex(8))
        self._turns[turn.id] = turn
        turn.task = asyncio.create_task(self._run(turn, work))
        # A turn stopped before its first step never runs any of `_run`, so its end is
        # recorded here instead, and anyone watching still gets a last event.
        turn.task.add_done_callback(lambda _: self._close_if_never_started(turn))
        self._let_go_of_old_turns()
        return turn

    def get(self, turn_id: str) -> Turn | None:
        return self._turns.get(turn_id)

    def current(self) -> Turn | None:
        """The earliest turn not yet done: the one running, while later ones wait."""
        return next((turn for turn in self._turns.values() if not turn.done), None)

    @staticmethod
    async def _run(turn: Turn, work: Work) -> None:
        # Whatever ends the turn, everyone watching is told how with a last event.
        try:
            await work(turn)
        except asyncio.CancelledError as error:
            if error.args != (STOPPED_BY_OPERATOR,):
                # Not the operator, such as the program shutting down: pass it on, as
                # asyncio expects of a task that is cancelled.
                await turn.add("error", {"message": stopped_message(error)})
                raise
            # The operator's stop ends here, at the top of the turn's own task, so this
            # task undoes the cancel it asked for.
            asyncio.current_task().uncancel()  # type: ignore[union-attr]
            await turn.add("stopped", {})
        except Exception as error:
            await turn.add("error", {"message": stopped_message(error)})
        finally:
            await turn.finish()

    def _close_if_never_started(self, turn: Turn) -> None:
        if not turn.done:
            closing = asyncio.get_running_loop().create_task(self._close(turn))
            self._closing.add(closing)
            closing.add_done_callback(self._closing.discard)

    @staticmethod
    async def _close(turn: Turn) -> None:
        await turn.add("stopped", {})
        await turn.finish()

    def _let_go_of_old_turns(self) -> None:
        finished = [turn_id for turn_id, turn in self._turns.items() if turn.done]
        for turn_id in finished[: max(0, len(finished) - KEPT_TURNS)]:
            del self._turns[turn_id]
