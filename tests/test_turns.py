"""Turns run on the server, apart from the page that started them, through the HTTP API.

A turn's events are numbered and kept, so a page can rejoin from the start or, with the
server-sent events `Last-Event-ID` header, from where it left off. Closing a page only
stops watching; stopping the work is its own request.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from scripted_model import ScriptedModel, WaitingModel

from homunculus.activity import Activity
from homunculus.approvals import Approvals
from homunculus.model import Message, ToolCall, Usage
from homunculus.server import create_app


@asynccontextmanager
async def client_for(model: ScriptedModel, tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(model, [], Activity(tmp_path), Approvals(tmp_path / "approvals.json"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def parse(body: str) -> list[dict[str, Any]]:
    """Server-sent events as dicts of their fields."""
    found = []
    for block in body.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        found.append(fields)
    return found


async def until_done(client: httpx.AsyncClient) -> None:
    while (await client.get("/api/turns/current")).status_code == 200:
        await asyncio.sleep(0.01)


async def test_a_turn_carries_on_after_the_page_goes_away(tmp_path: Path) -> None:
    model = WaitingModel()
    async with client_for(model, tmp_path) as client:
        page = asyncio.create_task(client.post("/api/chat", json={"message": "Plan my week"}))
        await model.started.wait()

        page.cancel()  # the page is closed mid-reply
        model.finish.set()
        await until_done(client)

        history = (await client.get("/api/history")).json()
        [job] = (await client.get("/api/activity")).json()
    assert history[-1] == {"role": "assistant", "content": "Working on it."}
    assert (job["reply"], job["error"]) == ("Working on it.", None)


async def test_a_page_can_rejoin_a_running_turn_from_the_start(tmp_path: Path) -> None:
    model = WaitingModel()
    async with client_for(model, tmp_path) as client:
        page = asyncio.create_task(client.post("/api/chat", json={"message": "Plan my week"}))
        await model.started.wait()
        page.cancel()

        current = (await client.get("/api/turns/current")).json()
        rejoined = asyncio.create_task(client.get(f"/api/turns/{current['id']}/events"))
        model.finish.set()
        events = parse((await rejoined).text)

    assert [e["event"] for e in events] == ["turn", "delta", "delta", "done"]
    assert [int(e["id"]) for e in events] == [1, 2, 3, 4]
    assert events[0]["data"] == f'{{"id": "{current["id"]}", "message": "Plan my week"}}'
    assert events[2]["data"] == '{"text": " on it."}'


async def test_a_page_that_reconnects_gets_only_the_events_it_missed(tmp_path: Path) -> None:
    async with client_for(ScriptedModel([["Hello", " there."]]), tmp_path) as client:
        first = parse((await client.post("/api/chat", json={"message": "Hi"})).text)
        turn = first[0]["data"].split('"')[3]

        again = await client.get(f"/api/turns/{turn}/events", headers={"Last-Event-ID": "2"})

    assert [(e["id"], e["event"]) for e in parse(again.text)] == [("3", "delta"), ("4", "done")]


async def test_stop_ends_a_running_turn_and_leaves_the_conversation_as_it_was(
    tmp_path: Path,
) -> None:
    model = WaitingModel()
    async with client_for(model, tmp_path) as client:
        page = asyncio.create_task(client.post("/api/chat", json={"message": "Plan my week"}))
        await model.started.wait()
        current = (await client.get("/api/turns/current")).json()

        stopped = await client.post(f"/api/turns/{current['id']}/stop")
        events = parse((await page).text)
        history = (await client.get("/api/history")).json()
        [job] = (await client.get("/api/activity")).json()

    assert stopped.status_code == 202
    assert events[-1]["event"] == "stopped"
    assert history == []
    assert job["error"] == "The turn was stopped by the operator."


async def test_only_a_running_turn_can_be_stopped_or_named_current(tmp_path: Path) -> None:
    async with client_for(ScriptedModel([["Hello."]]), tmp_path) as client:
        # No turn running is an answer with nothing in it, not an error.
        assert (await client.get("/api/turns/current")).status_code == 204
        first = parse((await client.post("/api/chat", json={"message": "Hi"})).text)
        turn = first[0]["data"].split('"')[3]

        assert (await client.post(f"/api/turns/{turn}/stop")).status_code == 409
        assert (await client.post("/api/turns/nope/stop")).status_code == 404
        assert (await client.get("/api/turns/nope/events")).status_code == 404
        assert (await client.get("/api/turns/current")).status_code == 204


class Crashes(ScriptedModel):
    async def stream(
        self, messages: list[Message], tools: list[dict[str, Any]]
    ) -> AsyncIterator[str | ToolCall | Usage]:
        yield "Let me"
        raise RuntimeError("a bug")


async def test_a_turn_that_fails_unexpectedly_ends_its_stream_with_an_error(
    tmp_path: Path,
) -> None:
    async with client_for(Crashes([]), tmp_path) as client:
        events = parse((await client.post("/api/chat", json={"message": "Hi"})).text)

    assert [e["event"] for e in events] == ["turn", "delta", "error"]
    assert events[-1]["data"] == (
        '{"message": "The turn stopped before it finished (RuntimeError: a bug)."}'
    )


async def test_a_last_event_id_that_is_not_a_number_starts_from_the_beginning(
    tmp_path: Path,
) -> None:
    async with client_for(ScriptedModel([["Hello."]]), tmp_path) as client:
        first = parse((await client.post("/api/chat", json={"message": "Hi"})).text)
        turn = first[0]["data"].split('"')[3]

        again = await client.get(f"/api/turns/{turn}/events", headers={"Last-Event-ID": "abc"})

    assert [e["id"] for e in parse(again.text)] == ["1", "2", "3"]
