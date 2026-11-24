"""The waker: when the agent works on its own, and how that work gives way to the operator.

The Waker is driven by a fake clock. Unprompted jobs run through the real server with a
scripted model; only the model and the clock are replaced.
"""

import asyncio
import json
import threading
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import mcp_types as mcp
import pytest
from scripted_model import Reply, ScriptedModel

from homunculus import gate, loop
from homunculus.activity import Activity
from homunculus.approvals import Approvals
from homunculus.limits import Limits
from homunculus.memory import Memory
from homunculus.model import Message, ToolCall
from homunculus.server import create_app
from homunculus.tools import Tool
from homunculus.tools.files import file_tools
from homunculus.waker import Waker, WakerSettings

ZONE = ZoneInfo("Asia/Kolkata")


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 25, 9, 0, tzinfo=ZONE)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


@pytest.fixture
def clock() -> Clock:
    return Clock()


def make_waker(tmp_path: Path, clock: Clock, **limits: Any) -> Waker:
    return Waker(
        tmp_path / "waker.json",
        WakerSettings(every_minutes=60, time_zone="Asia/Kolkata"),
        Limits(**limits),
        clock=clock,
    )


def tool_named(waker: Waker, name: str) -> Tool:
    return next(tool for tool in waker.tools() if tool.name == name)


def set_wake(waker: Waker, at: str, why: str = "check the draft") -> str:
    return tool_named(waker, "wake_at").call({"at": at, "why": why}).content[0].text  # type: ignore[union-attr]


# The timer


def test_the_timer_wakes_the_agent_once_an_hour(tmp_path: Path, clock: Clock) -> None:
    waker = make_waker(tmp_path, clock)

    assert waker.take() is None
    clock.advance(minutes=59)
    assert waker.take() is None
    clock.advance(minutes=1)
    assert waker.take() == "the hourly timer"
    assert waker.take() is None


def test_after_a_long_sleep_only_one_wake_up_runs(tmp_path: Path, clock: Clock) -> None:
    waker = make_waker(tmp_path, clock)

    clock.advance(hours=8)

    assert waker.take() == "the hourly timer"
    assert waker.take() is None


def test_no_more_than_the_daily_number_of_wake_ups_run(tmp_path: Path, clock: Clock) -> None:
    waker = make_waker(tmp_path, clock, wakeups_per_day=2)
    taken = []
    for _ in range(4):
        clock.advance(hours=1)
        taken.append(waker.take())

    assert taken == ["the hourly timer", "the hourly timer", None, None]
    assert waker.status()["today"] == {"woken": 2, "cap": 2, "skipped_busy": 0}

    clock.now = datetime(2026, 11, 26, 9, 30, tzinfo=ZONE)
    assert waker.take() == "the hourly timer"


def test_pause_stops_wake_ups_until_resumed_and_survives_a_restart(
    tmp_path: Path, clock: Clock
) -> None:
    waker = make_waker(tmp_path, clock)
    waker.pause()
    clock.advance(hours=2)

    assert waker.take() is None
    assert make_waker(tmp_path, clock).status()["paused"] is True

    waker.resume()
    assert waker.take() == "the hourly timer"


def test_wake_now_comes_first_and_counts_toward_the_cap(tmp_path: Path, clock: Clock) -> None:
    waker = make_waker(tmp_path, clock, wakeups_per_day=1)

    waker.wake_now()

    assert waker.take() == "Wake now"
    waker.wake_now()
    assert waker.take() is None
    assert waker.status()["today"]["woken"] == 1


# The agent's own wake-ups


def test_the_agent_sets_its_own_wake_up_and_sees_the_list(tmp_path: Path, clock: Clock) -> None:
    waker = make_waker(tmp_path, clock)

    result = set_wake(waker, "2026-11-25 09:20", "check the draft")

    assert "2026-11-25 09:20" in result and "check the draft" in result
    clock.advance(minutes=19)
    assert waker.take() is None
    clock.advance(minutes=1)
    assert waker.take() == "a wake-up it set: check the draft"
    assert waker.status()["pending"] == []


def test_a_wake_up_resets_the_hourly_timer(tmp_path: Path, clock: Clock) -> None:
    waker = make_waker(tmp_path, clock)
    set_wake(waker, "2026-11-25 09:50")
    clock.advance(minutes=50)
    assert waker.take() is not None

    clock.advance(minutes=30)

    assert waker.take() is None


@pytest.mark.parametrize(
    ("at", "problem"),
    [
        ("2026-11-25 09:02", "at least 5 minutes"),
        ("2026-12-09 09:00", "at most 7 days"),
        ("tomorrow morning", "YYYY-MM-DD HH:MM"),
    ],
)
def test_a_wake_up_too_soon_too_late_or_unreadable_is_refused(
    tmp_path: Path, clock: Clock, at: str, problem: str
) -> None:
    waker = make_waker(tmp_path, clock)

    result = tool_named(waker, "wake_at").call({"at": at, "why": "x"})

    assert result.is_error
    assert problem in result.content[0].text  # type: ignore[union-attr]
    assert waker.status()["pending"] == []


def test_no_more_than_ten_wake_ups_wait_at_once(tmp_path: Path, clock: Clock) -> None:
    waker = make_waker(tmp_path, clock)
    for hour in range(10, 20):
        set_wake(waker, f"2026-11-25 {hour}:00")

    result = tool_named(waker, "wake_at").call({"at": "2026-11-25 21:00", "why": "x"})

    assert result.is_error
    assert "10" in result.content[0].text  # type: ignore[union-attr]


def test_a_wake_up_can_be_cancelled_by_its_id(tmp_path: Path, clock: Clock) -> None:
    waker = make_waker(tmp_path, clock)
    set_wake(waker, "2026-11-25 12:00", "first")
    set_wake(waker, "2026-11-25 13:00", "second")
    (first, _) = waker.status()["pending"]

    result = tool_named(waker, "cancel_wake").call({"id": first["id"]}).content[0].text  # type: ignore[union-attr]

    assert "first" not in result and "second" in result
    assert [w["why"] for w in waker.status()["pending"]] == ["second"]


def test_wake_ups_survive_a_restart(tmp_path: Path, clock: Clock) -> None:
    set_wake(make_waker(tmp_path, clock), "2026-11-25 12:00", "after lunch")

    again = make_waker(tmp_path, clock)

    assert [w["why"] for w in again.status()["pending"]] == ["after lunch"]


def test_the_gate_lets_the_agent_manage_its_own_wake_ups(tmp_path: Path, clock: Clock) -> None:
    waker = make_waker(tmp_path, clock)
    for name in ("wake_at", "cancel_wake"):
        call = ToolCall(id="c", name=name, arguments=json.dumps({"at": "x", "why": "y", "id": "z"}))
        assert gate.decide(call, tool_named(waker, name)) is gate.Answer.ACT


# Jobs that stop for the operator


async def test_a_job_stops_at_the_next_step_when_asked_and_says_why(tmp_path: Path) -> None:
    (tmp_path / "workspace").mkdir()
    reason: list[str] = []
    calls: list[Reply] = [
        [ToolCall(id=f"c{n}", name="list", arguments='{"path": "."}')] for n in range(3)
    ]
    model = ScriptedModel([*calls, ["Done."]])
    history: list[Message] = []

    items = []
    async for item in loop.run(
        model,
        file_tools(tmp_path / "workspace"),
        Activity(tmp_path),
        Approvals(tmp_path / "a.json"),
        history,
        "Work on your own.",
        unprompted=True,
        interruption=lambda: reason[0] if reason else None,
    ):
        items.append(item)
        if isinstance(item, loop.ToolStep):
            reason.append("operator")

    (stop,) = [i for i in items if isinstance(i, loop.Stopped)]
    assert stop.limit == "operator"
    assert stop.message == "Stopped for your message. Steps that already ran were not undone."
    assert len(model.received) == 1


# Unprompted jobs through the server


def server_with(
    tmp_path: Path, model: ScriptedModel, waker: Waker, tools: list[Tool] | None = None
) -> tuple[Any, Memory]:
    memory = Memory.open(tmp_path / "memory")
    (memory.root / "goals.md").write_text("# Goals\n\nFinish the novel draft by March.\n")
    (memory.root / "todo.md").write_text("# To do\n\n- Outline chapter 8.\n")
    memory.take_hand_edits()
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    app = create_app(
        model,
        tools if tools is not None else [*file_tools(workspace, memory), *waker.tools()],
        Activity(tmp_path),
        Approvals(tmp_path / "approvals.json"),
        memory=memory,
        waker=waker,
    )
    return app, memory


@pytest.fixture
def client_for() -> Iterator[Any]:
    clients: list[httpx.AsyncClient] = []

    def make(app: Any) -> httpx.AsyncClient:
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
        clients.append(client)
        return client

    yield make


async def test_a_wake_up_starts_fresh_with_the_goals_and_to_do_notes(
    tmp_path: Path, clock: Clock, client_for: Any
) -> None:
    model = ScriptedModel([["Hello."], ["Outlined chapter 8."]])
    waker = make_waker(tmp_path, clock)
    app, _ = server_with(tmp_path, model, waker)
    client = client_for(app)
    await client.post("/api/chat", json={"message": "My secret plan is X."})

    response = await client.post("/api/waker/wake")
    assert response.status_code == 202
    await app.state.waker_step()

    woke = model.received[-1]
    text = json.dumps(woke)
    assert "My secret plan is X." not in text
    assert "Finish the novel draft by March." in text
    assert "Outline chapter 8." in text
    assert "Wednesday 25 November 2026, 09:00" in text
    (job, _) = (await client.get("/api/activity")).json()
    assert job["unprompted"] is True
    assert job["reason"] == "Wake now"
    history = (await client.get("/api/history")).json()
    assert all("Outlined chapter 8." not in m["content"] for m in history)


async def test_a_wake_up_that_did_nothing_is_marked_quiet(
    tmp_path: Path, clock: Clock, client_for: Any
) -> None:
    model = ScriptedModel([["Nothing needs doing right now."]])
    waker = make_waker(tmp_path, clock)
    app, _ = server_with(tmp_path, model, waker)
    clock.advance(hours=1)

    await app.state.waker_step()

    (job,) = (await client_for(app).get("/api/activity")).json()
    assert job["unprompted"] is True and job["quiet"] is True
    assert job["reason"] == "the hourly timer"


async def test_the_agent_keeps_its_to_do_notes_current(
    tmp_path: Path, clock: Clock, client_for: Any
) -> None:
    notes = "# To do\n\n- Outline chapter 8: done.\n- Next: draft scene 1.\n"
    # The notes were touched by the operator, so the agent changes them with edit, as the
    # hand-edit rule asks, rather than rewriting them whole.
    change = {
        "path": "memory/todo.md",
        "old": "- Outline chapter 8.",
        "new": "- Outline chapter 8: done.\n- Next: draft scene 1.",
    }
    edit = ToolCall(id="e", name="edit", arguments=json.dumps(change))
    model = ScriptedModel([[edit], ["Outlined chapter 8."]])
    waker = make_waker(tmp_path, clock)
    app, memory = server_with(tmp_path, model, waker)
    waker.wake_now()

    await app.state.waker_step()

    assert (memory.root / "todo.md").read_text() == notes
    (job,) = (await client_for(app).get("/api/activity")).json()
    assert job["steps"][0]["outcome"] == "ok"
    assert job["quiet"] is False


async def test_a_wake_up_due_while_the_operator_is_chatting_is_skipped(
    tmp_path: Path, clock: Clock
) -> None:
    model = ScriptedModel([])
    waker = make_waker(tmp_path, clock)
    app, _ = server_with(tmp_path, model, waker)
    clock.advance(hours=1)

    async with app.state.busy:
        await app.state.waker_step()

    assert model.received == []
    assert waker.status()["today"]["skipped_busy"] == 1
    assert waker.take() is None


async def test_the_operators_message_stops_an_unprompted_job_and_goes_first(
    tmp_path: Path, clock: Clock, client_for: Any
) -> None:
    started, release = threading.Event(), threading.Event()

    def slow(arguments: dict[str, Any]) -> str:
        started.set()
        release.wait(timeout=10)
        return "done"

    definition = mcp.Tool(
        name="slow",
        description="A slow step.",
        input_schema={"type": "object", "properties": {}},
        annotations=mcp.ToolAnnotations(read_only_hint=True),
    )
    step = ToolCall(id="s", name="slow", arguments="{}")
    model = ScriptedModel([[step], ["Hello, you."]])
    waker = make_waker(tmp_path, clock)
    app, _ = server_with(tmp_path, model, waker, tools=[Tool(definition, slow)])
    client = client_for(app)
    waker.wake_now()

    job = asyncio.create_task(app.state.waker_step())
    await asyncio.to_thread(started.wait, 10)
    reply = asyncio.create_task(client.post("/api/chat", json={"message": "Hi"}))
    await asyncio.sleep(0.2)
    release.set()
    await job
    answered = await reply

    assert "Hello, you." in answered.text
    jobs = (await client.get("/api/activity")).json()
    unprompted = next(j for j in jobs if j["unprompted"])
    assert unprompted["limit"] == "operator"


async def test_pause_stops_a_running_job_and_wake_now_while_paused_still_runs(
    tmp_path: Path, clock: Clock, client_for: Any
) -> None:
    started, release = threading.Event(), threading.Event()

    def slow(arguments: dict[str, Any]) -> str:
        started.set()
        release.wait(timeout=10)
        return "done"

    definition = mcp.Tool(
        name="slow",
        description="A slow step.",
        input_schema={"type": "object", "properties": {}},
        annotations=mcp.ToolAnnotations(read_only_hint=True),
    )
    model = ScriptedModel([[ToolCall(id="s", name="slow", arguments="{}")], ["More."]])
    waker = make_waker(tmp_path, clock)
    app, _ = server_with(tmp_path, model, waker, tools=[Tool(definition, slow)])
    client = client_for(app)
    waker.wake_now()

    job = asyncio.create_task(app.state.waker_step())
    await asyncio.to_thread(started.wait, 10)
    assert (await client.post("/api/waker/pause")).json()["paused"] is True
    release.set()
    await job

    (stopped,) = (await client.get("/api/activity")).json()
    assert stopped["limit"] == "paused"
    assert "Paused by you. Steps that already ran were not undone." in stopped["reply"]
    status = (await client.get("/api/waker")).json()
    assert status["paused"] is True
    assert (await client.post("/api/waker/resume")).json()["paused"] is False


async def test_the_waker_status_lists_what_is_planned(
    tmp_path: Path, clock: Clock, client_for: Any
) -> None:
    waker = make_waker(tmp_path, clock)
    set_wake(waker, "2026-11-25 12:00", "after lunch")
    app, _ = server_with(tmp_path, ScriptedModel([]), waker)

    status = (await client_for(app).get("/api/waker")).json()

    assert status["paused"] is False
    assert status["next_timer"] == "2026-11-25 10:00"
    assert [(w["at"], w["why"]) for w in status["pending"]] == [("2026-11-25 12:00", "after lunch")]
    assert status["today"] == {"woken": 0, "cap": 24, "skipped_busy": 0}
    assert status["working"] is False


# To-do notes in memory


def test_the_to_do_notes_are_in_every_prompt_within_a_cap(tmp_path: Path) -> None:
    memory = Memory.open(tmp_path / "memory")

    assert (memory.root / "todo.md").read_text() == "# To do\n"
    assert "### To-do notes (8 of 2,000 characters)" in memory.context()


# On its own, the agent asks before changing files it did not make on its own


def file_call(name: str, **arguments: str) -> ToolCall:
    return ToolCall(id="f", name=name, arguments=json.dumps(arguments))


@pytest.fixture
def files_in(tmp_path: Path) -> tuple[dict[str, Tool], Memory]:
    workspace = tmp_path / "workspace"
    (workspace / "book").mkdir(parents=True)
    (workspace / "book" / "chapter-7.md").write_text("The operator's chapter.\n")
    memory = Memory.open(tmp_path / "memory")
    return {tool.name: tool for tool in file_tools(workspace, memory)}, memory


@pytest.mark.parametrize(
    ("name", "arguments", "in_chat"),
    [
        ("write", {"path": "book/chapter-7.md", "content": "Mine now."}, gate.Answer.ACT),
        ("edit", {"path": "book/chapter-7.md", "old": "The", "new": "A"}, gate.Answer.ACT),
        ("rename", {"path": "book/chapter-7.md", "to": "book/old.md"}, gate.Answer.ACT),
        # Deleting already waits for the operator, whoever asked.
        ("delete", {"path": "book/chapter-7.md"}, gate.Answer.ASK),
    ],
)
def test_on_its_own_changing_an_existing_file_waits_for_the_operator(
    files_in: tuple[dict[str, Tool], Memory],
    name: str,
    arguments: dict[str, str],
    in_chat: gate.Answer,
) -> None:
    tools, _ = files_in
    call = file_call(name, **arguments)

    assert gate.decide(call, tools[name], unprompted=True, own=set()) is gate.Answer.ASK
    # Asked for in chat, the same change is decided as it always was.
    assert gate.decide(call, tools[name]) is in_chat


def test_on_its_own_new_files_its_own_files_and_its_notes_go_ahead(
    files_in: tuple[dict[str, Tool], Memory],
) -> None:
    tools, _ = files_in
    new = file_call("write", path="book/chapter-8-outline.md", content="Beats.")
    own = file_call("write", path="book/chapter-7.md", content="Its own draft.")
    notes = file_call("edit", path="memory/todo.md", old="# To do", new="# To do\n- Next.")

    assert gate.decide(new, tools["write"], unprompted=True, own=set()) is gate.Answer.ACT
    assert (
        gate.decide(own, tools["write"], unprompted=True, own={"book/chapter-7.md"})
        is gate.Answer.ACT
    )
    assert gate.decide(notes, tools["edit"], unprompted=True, own=set()) is gate.Answer.ACT


async def test_a_file_made_on_its_own_can_be_changed_on_a_later_wake_up(
    tmp_path: Path, clock: Clock, client_for: Any
) -> None:
    create = file_call("write", path="draft.md", content="First go.")
    revise = file_call("write", path="draft.md", content="Second go.")
    theirs = file_call("write", path="theirs.md", content="Overwritten.")
    model = ScriptedModel([[create], ["Drafted."], [revise, theirs], ["Revised."]])
    waker = make_waker(tmp_path, clock)
    app, _ = server_with(tmp_path, model, waker)
    workspace = tmp_path / "workspace"
    (workspace / "theirs.md").write_text("The operator's.\n")

    waker.wake_now()
    await app.state.waker_step()
    waker.wake_now()
    await app.state.waker_step()

    assert (workspace / "draft.md").read_text() == "Second go."
    assert (workspace / "theirs.md").read_text() == "The operator's.\n"
    approvals = (await client_for(app).get("/api/approvals")).json()
    assert [(a["tool"], a["status"]) for a in approvals] == [("write", "pending")]
    # Remembered across a restart.
    assert "draft.md" in make_waker(tmp_path, clock).own_files


def test_without_a_waker_its_status_is_empty_not_an_error(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    app = create_app(ScriptedModel([]), [], Activity(tmp_path), Approvals(tmp_path / "a.json"))

    response = TestClient(app).get("/api/waker")

    assert response.status_code == 204
