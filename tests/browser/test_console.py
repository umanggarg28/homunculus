"""The web console in a real browser, against the real server.

Only the model is replaced, by the scripted stand-in. These tests need the
console to be built (`npm run build` in web/) and a Playwright browser, so they
run on their own: `uv run pytest -m browser`.
"""

import asyncio
import json
import re
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import mcp_types as mcp
import pytest
from fastapi import FastAPI
from playwright.sync_api import Locator, Page, expect, sync_playwright
from scripted_model import ScriptedModel
from serve import CONSOLE, start_server

from homunculus.activity import Activity
from homunculus.approvals import Approvals
from homunculus.model import Message, ToolCall, Usage
from homunculus.server import create_app
from homunculus.tools import Tool
from homunculus.tools.files import file_tools

pytestmark = [
    pytest.mark.browser,
    pytest.mark.enable_socket,
    pytest.mark.skipif(not (CONSOLE / "index.html").is_file(), reason="console not built"),
]


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    folder = tmp_path / "workspace"
    folder.mkdir()
    return folder


def console_app(model: ScriptedModel, tools: list[Tool], tmp_path: Path) -> FastAPI:
    return create_app(
        model,
        tools,
        Activity(tmp_path),
        Approvals(tmp_path / "approvals.json"),
        console=CONSOLE,
    )


@pytest.fixture
def console_url(tmp_path: Path, workspace: Path) -> Iterator[str]:
    note = {"path": "priorities.md", "content": "1. Rest"}
    model = ScriptedModel(
        [
            [ToolCall(id="call_1", name="write", arguments=json.dumps(note))],
            ["Saved ", "**priorities.md**."],
        ]
    )
    yield from start_server(console_app(model, file_tools(workspace), tmp_path))


# The console is one screen: the conversation in the middle, and what needs approval
# and what the agent did beside it. The same words can show in more than one place
# (a reply is also in its activity row), so each check names where it looks.
def thread(page: Page) -> Locator:
    return page.get_by_role("main")


def side(page: Page) -> Locator:
    return page.get_by_role("complementary", name="Needs approval and activity")


def hold(button: Locator, ms: int = 900) -> None:
    """Press and hold, the way allowing a request works: a click alone does nothing."""
    button.hover()
    button.page.mouse.down()
    button.page.wait_for_timeout(ms)
    button.page.mouse.up()


def status(page: Page) -> Locator:
    """The status pill in the top bar, which says what the agent is doing now."""
    return page.locator("#status")


def test_the_opening_plays_on_the_first_visit_and_a_reload_skips_it(console_url: str) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(console_url)
        root = page.locator("html")
        expect(root).to_have_attribute("data-intro", "0")
        expect(root).to_have_attribute("data-intro", "done", timeout=8_000)
        expect(page.get_by_role("main").locator("textarea")).to_be_visible()

        page.reload(wait_until="domcontentloaded")

        assert root.get_attribute("data-intro") == "done"
        browser.close()


def test_sending_a_message_shows_the_tool_step_and_the_reply(
    page: Page, console_url: str, workspace: Path
) -> None:
    page.goto(f"{console_url}/chat")
    expect(page).to_have_url(f"{console_url}/")
    expect(status(page)).to_have_text("Ready")

    page.locator("textarea").fill("Save my priorities")
    page.locator("textarea").press("Enter")

    expect(thread(page).get_by_text("Saved priorities.md.")).to_be_visible()
    step = thread(page).get_by_role("button", name=re.compile(r"^Step 1, Wrote priorities\.md"))
    expect(step).to_be_visible()
    expect(step).not_to_have_class(re.compile(r"is-bad"))
    assert (workspace / "priorities.md").read_text() == "1. Rest"
    expect(status(page)).to_have_text("Ready")

    page.reload()

    expect(thread(page).get_by_text("Save my priorities")).to_be_visible()
    expect(thread(page).get_by_text("Saved priorities.md.")).to_be_visible()


def test_approving_a_request_runs_it_and_the_agent_carries_on(page: Page, tmp_path: Path) -> None:
    sent: list[dict[str, object]] = []

    def send_email(arguments: dict[str, object]) -> str:
        sent.append(arguments)
        return "Sent."

    tool = Tool(
        mcp.Tool(name="send_email", description="Send an email.", input_schema={"type": "object"}),
        send_email,
    )
    email = ToolCall(id="call_1", name="send_email", arguments='{"to": "sam@example.com"}')
    model = ScriptedModel([[email], ["I have asked you first."], ["Sam is emailed."]])
    for url in start_server(console_app(model, [tool], tmp_path)):
        page.goto(f"{url}/chat")
        page.locator("textarea").fill("Email Sam")
        page.locator("textarea").press("Enter")
        expect(thread(page).get_by_text("I have asked you first.")).to_be_visible()
        held = thread(page).get_by_role("button", name=re.compile(r"^Step 1, Needs your approval"))
        expect(held).to_be_visible()
        expect(status(page)).to_have_text("Needs approval")

        expect(side(page).get_by_text("sam@example.com").first).to_be_visible()
        allow = side(page).get_by_role("button", name=re.compile(r"Hold to allow once"))
        allow.click()
        page.wait_for_timeout(500)
        assert sent == [], "a click alone must not allow it"
        hold(allow)

        expect(side(page).get_by_text("Result: Sent.")).to_be_visible()
        assert sent == [{"to": "sam@example.com"}]
        expect(thread(page).get_by_text("Sam is emailed.")).to_be_visible()


def test_a_piece_of_work_can_be_rated_and_the_rating_is_kept(page: Page, console_url: str) -> None:
    page.goto(f"{console_url}/chat")
    page.locator("textarea").fill("Save my priorities")
    page.locator("textarea").press("Enter")
    expect(thread(page).get_by_text("Saved priorities.md.")).to_be_visible()

    # A job opens into its steps, its reply and the rating.
    side(page).get_by_role("button", name="Save my priorities").click()
    expect(side(page).get_by_text("Wrote priorities.md", exact=True)).to_be_visible()
    page.get_by_role("button", name="not good work").click()
    page.get_by_placeholder("What could be better? (optional)").fill("Ask before saving")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_text("“Ask before saving”")).to_be_visible()

    page.reload()

    side(page).get_by_role("button", name="Save my priorities").click()
    expect(page.get_by_text("“Ask before saving”")).to_be_visible()
    # A new rating starts with no reason, so the old one is not carried over.
    page.get_by_role("button", name="good work", exact=True).click()
    page.get_by_placeholder("What was good? (optional)").fill("Clear plan")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_text("“Clear plan”")).to_be_visible()
    expect(page.get_by_text("“Ask before saving”")).to_have_count(0)


class PausingModel(ScriptedModel):
    """Replies in two halves with a pause between, long enough to reload the page."""

    def __init__(self, pause: float) -> None:
        super().__init__([])
        self.pause = pause

    async def stream(
        self, messages: list[Message], tools: list[dict[str, Any]]
    ) -> AsyncIterator[str | ToolCall | Usage]:
        yield "Working"
        await asyncio.sleep(self.pause)
        yield " on it."


def test_reloading_during_a_turn_still_shows_the_whole_reply(
    page: Page, tmp_path: Path, workspace: Path
) -> None:
    for url in start_server(console_app(PausingModel(3), file_tools(workspace), tmp_path)):
        page.goto(f"{url}/chat")
        page.locator("textarea").fill("Plan my week")
        page.locator("textarea").press("Enter")
        expect(thread(page).get_by_text("Working", exact=True)).to_be_visible()
        expect(status(page)).to_have_text("Thinking")

        page.reload()

        # A reload skips the opening and rejoins the turn where it is.
        expect(thread(page).get_by_text("Plan my week")).to_be_visible()
        expect(status(page)).to_have_text("Thinking")
        expect(thread(page).get_by_text("Working on it.")).to_be_visible(timeout=10_000)


def test_stop_ends_the_turn_on_the_server(page: Page, tmp_path: Path, workspace: Path) -> None:
    for url in start_server(console_app(PausingModel(30), file_tools(workspace), tmp_path)):
        page.goto(f"{url}/chat")
        page.locator("textarea").fill("Plan my week")
        page.locator("textarea").press("Enter")
        expect(thread(page).get_by_text("Working", exact=True)).to_be_visible()

        page.get_by_role("button", name="Stop").click()

        expect(thread(page).get_by_text("stopped by you")).to_be_visible()
        expect(status(page)).to_have_text("Stopped")
        side(page).get_by_role("button", name="Plan my week").click()
        expect(side(page).get_by_text("The turn was stopped by the operator.")).to_be_visible()


def test_now_loads_without_errors_in_the_browser_console(page: Page, console_url: str) -> None:
    errors: list[str] = []
    page.on(
        "console", lambda message: errors.append(message.text) if message.type == "error" else None
    )
    page.on("pageerror", lambda error: errors.append(str(error)))

    page.goto(console_url)
    expect(status(page)).to_have_text("Ready")
    expect(side(page).get_by_role("heading", name="Activity")).to_be_visible()
    page.wait_for_timeout(500)

    assert errors == []


def test_activity_from_a_server_without_costs_still_shows(page: Page, console_url: str) -> None:
    # A job as a server from before costs were recorded describes it: no cost, no limit.
    job = {
        "job": "a" * 32,
        "span": "b" * 16,
        "started": "2026-11-20T09:00:00+00:00",
        "ended": "2026-11-20T09:00:05+00:00",
        "message": "Tidy my notes.",
        "notice": False,
        "steps": [],
        "reply": "Done.",
        "error": None,
        "feedback": None,
    }
    page.route("**/api/activity", lambda route: route.fulfill(json=[job]))
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    page.goto(console_url)

    expect(side(page).get_by_role("button", name="Tidy my notes.")).to_be_visible()
    assert errors == []


def test_pause_and_wake_now_from_the_top_bar(page: Page, tmp_path: Path, workspace: Path) -> None:
    from homunculus.limits import Limits
    from homunculus.memory import Memory
    from homunculus.waker import Waker, WakerSettings

    memory = Memory.open(tmp_path / "memory")
    waker = Waker(tmp_path / "waker.json", WakerSettings(), Limits())
    model = ScriptedModel([["Nothing needs doing right now."]])
    app = create_app(
        model,
        [*file_tools(workspace, memory), *waker.tools()],
        Activity(tmp_path),
        Approvals(tmp_path / "approvals.json"),
        console=CONSOLE,
        memory=memory,
        waker=waker,
    )
    for url in start_server(app):
        page.goto(url)
        bar = page.get_by_role("group", name="Work on its own")

        bar.get_by_role("button", name="Pause").click()
        expect(status(page)).to_have_text("Paused")
        expect(bar.get_by_role("button", name="Resume")).to_have_attribute("aria-pressed", "true")

        bar.get_by_role("button", name="Resume").click()
        bar.get_by_role("button", name="Wake now").click()

        job = side(page).get_by_role("button", name="Nothing needed doing")
        expect(job).to_be_visible(timeout=15_000)
        expect(side(page).get_by_text("On its own (Wake now)")).to_be_visible()
