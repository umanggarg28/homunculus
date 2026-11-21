"""The Memory screen in a real browser, against the real server and a real memory folder.

Only the model is replaced, and it is never called: these tests read and edit memory
through the console. The pages are fictional.
"""

import re
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from scripted_model import ScriptedModel
from serve import CONSOLE, start_server

from homunculus.activity import Activity
from homunculus.approvals import Approvals
from homunculus.memory import Memory
from homunculus.server import create_app

pytestmark = [
    pytest.mark.browser,
    pytest.mark.enable_socket,
    pytest.mark.skipif(not (CONSOLE / "index.html").is_file(), reason="console not built"),
]

BOOK = """---
title: The book
summary: The operator's novel and its deadline.
---

~~The draft stands at 30,000 words.~~[^1]
The draft stands at 38,200 words.[^1]
The draft is due in March.[^1]

## Inferred

Chapter 7 is the midpoint.[^2]
The ending is not written yet.[^1]

## See also

- [Tea](tea.md)

[^1]: raw/conversations/2026-11-13.md
[^2]: raw/book-notes/structure.md
"""

TEA = """---
title: Tea
summary: How the operator takes their tea.
---

The operator drinks green tea every morning.[^1]

[^1]: raw/conversations/2026-11-14.md
"""


@pytest.fixture
def memory(tmp_path: Path) -> Memory:
    opened = Memory.open(tmp_path / "memory")
    sources = {
        "raw/conversations/2026-11-13.md": "The draft is at 38,200 words, due in March.\n",
        "raw/conversations/2026-11-14.md": "I drink green tea every morning.\n",
        "raw/book-notes/structure.md": "Twelve chapters. Chapter 7 is where the light goes out.\n",
    }
    for path, text in sources.items():
        (opened.root / path).parent.mkdir(parents=True, exist_ok=True)
        (opened.root / path).write_text(text)
    (opened.root / "wiki" / "book.md").write_text(BOOK)
    (opened.root / "wiki" / "tea.md").write_text(TEA)
    (opened.root / "profile.md").write_text(
        "# Profile\n\nThe operator writes in the mornings.[^1]\n\n## How I like to work\n\n"
        "[^1]: raw/conversations/2026-11-13.md\n"
    )
    opened.take_hand_edits()
    return opened


@pytest.fixture
def url(tmp_path: Path, memory: Memory) -> Iterator[str]:
    app = create_app(
        ScriptedModel([]),
        [],
        Activity(tmp_path),
        Approvals(tmp_path / "approvals.json"),
        console=CONSOLE,
        memory=memory,
    )
    yield from start_server(app)


def book(memory: Memory) -> str:
    return (memory.root / "wiki" / "book.md").read_text()


def last_author(memory: Memory) -> str:
    return subprocess.run(
        ["git", "log", "-1", "--format=%an"], cwd=memory.root, capture_output=True, text=True
    ).stdout.strip()


def test_the_nav_opens_memory_and_back_returns(page: Page, url: str) -> None:
    page.goto(url)

    page.get_by_role("navigation", name="Main").get_by_role("link", name="Memory").click()

    expect(page).to_have_url(f"{url}/memory/about")
    expect(page.get_by_role("heading", name="About you")).to_be_visible()
    page.go_back()
    expect(page).to_have_url(f"{url}/")


def test_a_page_shows_each_fact_with_its_numbered_source(page: Page, url: str) -> None:
    page.goto(f"{url}/memory")
    pages = page.get_by_role("navigation", name="Memory pages")
    expect(pages.get_by_role("link", name=re.compile("^Tea"))).to_be_visible()

    pages.get_by_role("link", name=re.compile("^The book")).click()

    expect(page).to_have_url(f"{url}/memory/wiki/book.md")
    main = page.get_by_role("main")
    expect(main.get_by_role("heading", name="The book", level=1)).to_be_visible()
    expect(main.get_by_text("The draft stands at 38,200 words.")).to_be_visible()
    # An outdated line stays in the file and is shown only on request, struck through.
    expect(main.get_by_text("The draft stands at 30,000 words.")).to_be_hidden()
    main.get_by_role("button", name="Show earlier lines (1)").click()
    expect(main.locator("s", has_text="The draft stands at 30,000 words.")).to_be_visible()

    first = main.get_by_role("button", name="Source 1: raw/conversations/2026-11-13.md").first
    first.click()
    expect(first).to_have_attribute("aria-pressed", "true")
    sources = page.get_by_role("complementary", name="Sources")
    expect(sources.get_by_text("raw/conversations/2026-11-13.md")).to_be_visible()


def test_an_edit_is_saved_as_yours_keeps_the_old_line_and_can_be_undone(
    page: Page, url: str, memory: Memory
) -> None:
    page.goto(f"{url}/memory/wiki/book.md")
    main = page.get_by_role("main")
    main.get_by_role("button", name="Edit").click()
    editor = main.get_by_role("textbox", name="The book, as markdown")
    expect(editor).to_have_value(BOOK)

    editor.fill(BOOK.replace("stands at 38,200 words", "stands at 41,000 words"))
    main.get_by_role("button", name="Save").click()

    # The editor closes once the save is done.
    expect(editor).to_be_hidden()
    expect(main.get_by_text("The draft stands at 41,000 words.")).to_be_visible()
    saved = book(memory)
    # A sourced line you change stays in the file, struck through, before your new line.
    assert (
        "~~The draft stands at 38,200 words.~~[^1]\nThe draft stands at 41,000 words.[^1]" in saved
    )
    assert last_author(memory) == "Operator"

    undo = page.get_by_role("button", name=re.compile("^Undo"))
    expect(undo).to_be_focused()
    undo.click()

    expect(main.get_by_text("The draft stands at 38,200 words.")).to_be_visible()
    assert book(memory) == BOOK


def test_a_save_after_the_page_changed_is_refused_and_keeps_your_draft(
    page: Page, url: str, memory: Memory
) -> None:
    page.goto(f"{url}/memory/wiki/book.md")
    main = page.get_by_role("main")
    main.get_by_role("button", name="Edit").click()
    editor = main.get_by_role("textbox", name="The book, as markdown")
    draft = BOOK.replace("due in March", "due in April")
    editor.fill(draft)
    # The agent changes the page while the operator is still typing.
    newer = BOOK.replace("38,200", "39,000")
    (memory.root / "wiki" / "book.md").write_text(newer)

    main.get_by_role("button", name="Save").click()

    expect(main.get_by_role("alert")).to_contain_text("changed since you opened it")
    expect(editor).to_have_value(draft)
    assert book(memory) == newer

    main.get_by_role("button", name="Show the newer version").click()
    expect(main.get_by_text("The draft stands at 39,000 words.")).to_be_visible()


def test_guesses_on_about_you_can_be_confirmed_or_removed(
    page: Page, url: str, memory: Memory
) -> None:
    page.goto(f"{url}/memory/about")
    guesses = page.get_by_role("region", name="Guesses to check")
    expect(guesses.get_by_text("Chapter 7 is the midpoint.")).to_be_visible()

    guesses.get_by_role("button", name="Confirm “Chapter 7 is the midpoint.”").click()

    expect(guesses.get_by_text("Chapter 7 is the midpoint.")).to_be_hidden()
    body, inferred = book(memory).split("## Inferred")
    assert "Chapter 7 is the midpoint.[^2]" in body
    assert "Chapter 7" not in inferred

    guesses.get_by_role("button", name="Remove “The ending is not written yet.”").click()

    expect(page.get_by_text("No guesses to check.")).to_be_visible()
    assert "The ending is not written yet." not in book(memory)
    assert last_author(memory) == "Operator"


def test_about_you_lists_sources_the_profile_and_where_data_can_go(page: Page, url: str) -> None:
    page.goto(f"{url}/memory/about")

    read = page.get_by_role("region", name="What I read")
    expect(read.get_by_role("link", name="raw/book-notes/structure.md")).to_be_visible()
    profile = page.get_by_role("region", name="Your profile")
    expect(profile.get_by_text("The operator writes in the mornings.")).to_be_visible()
    expect(profile).to_contain_text("of 2,000 characters")
    egress = page.get_by_role("region", name="Where your data can go")
    expect(egress.get_by_text("openrouter.ai")).to_be_visible()

    read.get_by_role("link", name="raw/book-notes/structure.md").click()

    expect(page).to_have_url(f"{url}/memory/raw/book-notes/structure.md")
    expect(
        page.get_by_text("Twelve chapters. Chapter 7 is where the light goes out.")
    ).to_be_visible()


def test_the_profile_is_edited_on_about_you(page: Page, url: str, memory: Memory) -> None:
    page.goto(f"{url}/memory/about")
    profile = page.get_by_role("region", name="Your profile")
    profile.get_by_role("button", name="Edit").click()
    editor = profile.get_by_role("textbox", name="Your profile, as markdown")

    editor.fill(
        editor.input_value().replace(
            "## How I like to work\n", "## How I like to work\n\nShort replies.\n"
        )
    )
    profile.get_by_role("button", name="Save").click()

    expect(editor).to_be_hidden()
    expect(profile.get_by_text("Short replies.")).to_be_visible()
    assert "Short replies." in (memory.root / "profile.md").read_text()


def test_recent_changes_list_your_edits(page: Page, url: str, memory: Memory) -> None:
    page.goto(f"{url}/memory/about")
    page.get_by_role("region", name="Guesses to check").get_by_role(
        "button", name="Remove “The ending is not written yet.”"
    ).click()
    expect(
        page.get_by_role("region", name="Guesses to check").get_by_text("The ending")
    ).to_be_hidden()

    page.goto(f"{url}/memory/changes")

    changes = page.get_by_role("main")
    expect(changes.get_by_role("heading", name="Recent changes")).to_be_visible()
    expect(changes.get_by_text("You edited").first).to_be_visible()
    expect(changes.get_by_role("link", name="The book").first).to_be_visible()


def test_on_a_phone_pages_open_from_the_list_and_back_returns(page: Page, url: str) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{url}/memory/about")

    page.get_by_role("link", name="Pages").click()
    expect(page).to_have_url(f"{url}/memory/pages")
    page.get_by_role("navigation", name="Memory pages").get_by_role(
        "link", name=re.compile("^Tea")
    ).click()

    expect(page.get_by_role("heading", name="Tea", level=1)).to_be_visible()
    page.get_by_role("main").get_by_role("button", name="Back").click()
    expect(page).to_have_url(f"{url}/memory/pages")
