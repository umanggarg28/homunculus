"""The web console in a real browser, against the real server.

Only the model is replaced, by the scripted stand-in. These tests need the
console to be built (`npm run build` in web/) and a Playwright browser, so they
run on their own: `uv run pytest -m browser`.
"""

import json
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI
from playwright.sync_api import Page, expect, sync_playwright
from scripted_model import ScriptedModel

from homunculus.activity import ActivityLog
from homunculus.approvals import Approvals
from homunculus.model import ToolCall
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
        ActivityLog(tmp_path / "activity.jsonl"),
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
        # The one-time boot overlay is not what these tests are about.
        context.add_init_script("sessionStorage.setItem('homunculus_booted_v1', '1')")
        yield context.new_page()
        browser.close()


def test_sending_a_message_shows_the_tool_step_and_the_reply(
    page: Page, console_url: str, workspace: Path
) -> None:
    page.goto(f"{console_url}/chat")

    page.locator("textarea").fill("Save my priorities")
    page.locator("textarea").press("Enter")

    expect(page.get_by_text("Saved priorities.md.")).to_be_visible()
    expect(page.get_by_text("write", exact=True)).to_be_visible()
    expect(page.get_by_text("OK ·")).to_be_visible()
    assert (workspace / "priorities.md").read_text() == "1. Rest"

    page.reload()

    expect(page.get_by_text("Save my priorities")).to_be_visible()
    expect(page.get_by_text("Saved priorities.md.")).to_be_visible()


def test_approving_a_request_runs_it_and_the_agent_carries_on(page: Page, tmp_path: Path) -> None:
    sent: list[dict[str, object]] = []

    def send_email(arguments: dict[str, object]) -> str:
        sent.append(arguments)
        return "Sent."

    tool = Tool("send_email", "Send an email.", {"type": "object"}, send_email)
    email = ToolCall(id="call_1", name="send_email", arguments='{"to": "sam@example.com"}')
    model = ScriptedModel([[email], ["I have asked you first."], ["Sam is emailed."]])
    for url in start_server(console_app(model, [tool], tmp_path)):
        page.goto(f"{url}/chat")
        page.locator("textarea").fill("Email Sam")
        page.locator("textarea").press("Enter")
        expect(page.get_by_text("I have asked you first.")).to_be_visible()
        expect(page.get_by_text("ASK ·")).to_be_visible()

        page.get_by_role("link", name="APPROVALS").first.click()
        expect(page.get_by_text("sam@example.com")).to_be_visible()
        assert sent == []
        page.get_by_role("button", name="[approve]").click()

        expect(page.get_by_text("result: Sent.")).to_be_visible()
        assert sent == [{"to": "sam@example.com"}]
        page.get_by_role("link", name="CHAT").first.click()
        expect(page.get_by_text("Sam is emailed.")).to_be_visible()


def test_a_piece_of_work_can_be_rated_and_the_rating_is_kept(page: Page, console_url: str) -> None:
    page.goto(f"{console_url}/chat")
    page.locator("textarea").fill("Save my priorities")
    page.locator("textarea").press("Enter")
    expect(page.get_by_text("Saved priorities.md.")).to_be_visible()

    page.get_by_role("link", name="ACTIVITY").first.click()
    expect(page.get_by_text("Wrote priorities.md.")).to_be_visible()
    page.get_by_role("button", name="not good work").click()
    page.get_by_placeholder("what could be better? (optional)").fill("Ask before saving")
    page.get_by_role("button", name="[save]").click()
    expect(page.get_by_text("“Ask before saving”")).to_be_visible()

    page.reload()

    expect(page.get_by_text("“Ask before saving”")).to_be_visible()
