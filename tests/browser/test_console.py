"""The web console in a real browser, against the real server.

Only the model is replaced, by the scripted stand-in. These tests need the
console to be built (`npm run build` in web/) and a Playwright browser, so they
run on their own: `uv run pytest -m browser`.
"""

import asyncio
import json
import re
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import mcp_types as mcp
import pytest
import uvicorn
from fastapi import FastAPI
from playwright.sync_api import Locator, Page, expect, sync_playwright
from scripted_model import ScriptedModel

from homunculus.activity import Activity
from homunculus.approvals import Approvals
from homunculus.model import Message, ToolCall, Usage
from homunculus.server import create_app
from homunculus.tools import Tool
from homunculus.tools.files import file_tools

CONSOLE = Path(__file__).resolve().parents[2] / "web" / "dist"

pytestmark = [
    pytest.mark.browser,
    pytest.mark.enable_socket,
    pytest.mark.skipif(not (CONSOLE / "index.html").is_file(), reason="console not built"),
]


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    folder = tmp_path / "workspace"
    folder.mkdir()
    return folder


def start_server(app: FastAPI) -> Iterator[str]:
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


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


@pytest.fixture
def page() -> Iterator[Page]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context()
        # The opening plays once per session, and is not what most tests are about.
        context.add_init_script("sessionStorage.setItem('homunculus_booted_v1', '1')")
        yield context.new_page()
        browser.close()


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
