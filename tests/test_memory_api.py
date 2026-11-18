"""The memory over HTTP: what the console lists, reads and saves, through the real server."""

import re
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from scripted_model import ScriptedModel

from homunculus import egress
from homunculus.activity import Activity
from homunculus.approvals import Approvals
from homunculus.memory import CAPS, Memory
from homunculus.model import OPENROUTER_CHAT_URL
from homunculus.server import create_app
from homunculus.tools.files import file_tools

ROOT = Path(__file__).resolve().parents[1]

BOOK = """---
title: The book
summary: The operator's novel and its deadline.
---

The draft stands at 38,200 words.[^1]

## Inferred

Chapter 7 is the midpoint.[^1]
The ending is not written yet.[^1]

[^1]: raw/conversations/2026-11-13.md
"""


@pytest.fixture
def memory(tmp_path: Path) -> Memory:
    opened = Memory.open(tmp_path / "memory")
    source = opened.root / "raw" / "conversations" / "2026-11-13.md"
    source.parent.mkdir(parents=True)
    source.write_text("The draft is at 38,200 words.\n")
    (opened.root / "wiki" / "book.md").write_text(BOOK)
    opened.take_hand_edits()
    return opened


@pytest.fixture
def client(tmp_path: Path, memory: Memory) -> TestClient:
    app = create_app(
        ScriptedModel([]),
        [],
        Activity(tmp_path),
        Approvals(tmp_path / "approvals.json"),
        memory=memory,
    )
    return TestClient(app)


def git(memory: Memory, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=memory.root, capture_output=True, text=True, check=True
    ).stdout


def test_the_page_list_has_each_page_with_its_title_summary_and_guesses(
    client: TestClient,
) -> None:
    response = client.get("/api/memory/pages")

    assert response.status_code == 200
    assert response.json() == [
        {
            "path": "wiki/book.md",
            "title": "The book",
            "summary": "The operator's novel and its deadline.",
            "guesses": 2,
        }
    ]


def test_a_page_is_read_as_its_markdown_with_a_version(client: TestClient) -> None:
    response = client.get("/api/memory/pages/wiki/book.md")

    assert response.status_code == 200
    page = response.json()
    assert page["path"] == "wiki/book.md"
    assert page["text"] == BOOK
    assert page["version"]


@pytest.mark.parametrize(
    "path",
    [
        "wiki/missing.md",
        "wiki/index.md",
        "wiki/log.md",
        "raw/conversations/2026-11-13.md",
        "SCHEMA.md",
        "wiki/../raw/conversations/2026-11-13.md",
        "goals.md",
    ],
)
def test_only_wiki_pages_and_the_profile_are_pages(client: TestClient, path: str) -> None:
    assert client.get(f"/api/memory/pages/{path}").status_code == 404
    assert (
        client.put(f"/api/memory/pages/{path}", json={"text": "x", "version": ""}).status_code
        == 404
    )


def test_a_save_is_recorded_as_the_operators_hand_edit(client: TestClient, memory: Memory) -> None:
    version = client.get("/api/memory/pages/wiki/book.md").json()["version"]
    edited = BOOK.replace("title: The book", "title: The novel")

    response = client.put(
        "/api/memory/pages/wiki/book.md", json={"text": edited, "version": version}
    )

    assert response.status_code == 200
    assert response.json()["text"] == edited
    assert response.json()["version"] != version
    assert (memory.root / "wiki" / "book.md").read_text() == edited
    assert git(memory, "log", "-1", "--format=%an %s") == "Operator Hand edit: wiki/book.md\n"
    assert memory.hand_edited("wiki/book.md")
    # The index is rebuilt from the page, and the change is logged.
    assert "[The novel](book.md)" in (memory.root / "wiki" / "index.md").read_text()
    assert client.get("/api/memory/log").json()[0]["action"] == "hand edit"


def test_a_save_from_an_older_version_is_refused_and_changes_nothing(
    client: TestClient, memory: Memory
) -> None:
    version = client.get("/api/memory/pages/wiki/book.md").json()["version"]
    # The agent changes the page while the operator is still editing it.
    newer = BOOK.replace("38,200", "41,000")
    (memory.root / "wiki" / "book.md").write_text(newer)

    response = client.put("/api/memory/pages/wiki/book.md", json={"text": BOOK, "version": version})

    assert response.status_code == 409
    assert "changed" in response.json()["detail"]
    assert (memory.root / "wiki" / "book.md").read_text() == newer


def test_the_profile_can_be_read_and_saved_within_its_cap(
    client: TestClient, memory: Memory
) -> None:
    profile = client.get("/api/memory/pages/profile.md").json()
    text = profile["text"] + "I write in the mornings.\n"

    saved = client.put(
        "/api/memory/pages/profile.md", json={"text": text, "version": profile["version"]}
    )
    too_long = client.put(
        "/api/memory/pages/profile.md",
        json={"text": "x" * (CAPS["profile.md"] + 1), "version": saved.json()["version"]},
    )

    assert saved.status_code == 200
    assert (memory.root / "profile.md").read_text() == text
    assert too_long.status_code == 422
    assert "2,000" in too_long.json()["detail"]
    assert (memory.root / "profile.md").read_text() == text


def test_the_about_page_lists_each_source_and_when_the_profile_and_the_egress(
    client: TestClient,
) -> None:
    about = client.get("/api/memory/about").json()

    (source,) = about["sources"]
    assert source["path"] == "raw/conversations/2026-11-13.md"
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", source["added"])
    assert about["profile"]["text"].startswith("# Profile")
    assert about["profile"]["cap"] == CAPS["profile.md"]
    assert about["profile"]["version"]
    assert about["egress"] == [dict(route._asdict()) for route in egress.ROUTES]


def test_a_source_dropped_in_by_hand_is_listed_with_when_it_was_found(
    client: TestClient, memory: Memory
) -> None:
    (memory.root / "raw" / "notes.md").write_text("A note.\n")

    paths = [source["path"] for source in client.get("/api/memory/about").json()["sources"]]

    assert "raw/notes.md" in paths


def test_a_source_is_read_as_it_is(client: TestClient) -> None:
    response = client.get("/api/memory/sources/raw/conversations/2026-11-13.md")

    assert response.status_code == 200
    assert response.json() == {
        "path": "raw/conversations/2026-11-13.md",
        "text": "The draft is at 38,200 words.\n",
    }


@pytest.mark.parametrize(
    "path", ["raw/missing.md", "wiki/book.md", "profile.md", "raw/../wiki/book.md", "raw/"]
)
def test_only_files_under_raw_are_sources(client: TestClient, path: str) -> None:
    assert client.get(f"/api/memory/sources/{path}").status_code == 404


def test_the_log_is_listed_newest_first(client: TestClient, memory: Memory) -> None:
    (memory.root / "wiki" / "tea.md").write_text("---\ntitle: Tea\nsummary: Tea.\n---\n")
    memory.take_hand_edits()

    entries = client.get("/api/memory/log").json()

    assert entries[0]["path"] == "wiki/tea.md"
    assert entries[0]["action"] == "hand edit"
    assert re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", entries[0]["time"])
    assert [entry["path"] for entry in entries].index("wiki/book.md") > 0


def test_without_a_memory_there_is_no_memory_api(tmp_path: Path) -> None:
    app = create_app(ScriptedModel([]), [], Activity(tmp_path), Approvals(tmp_path / "a.json"))

    assert TestClient(app).get("/api/memory/pages").status_code == 404


def test_the_egress_list_matches_the_proxy_the_model_and_security_md() -> None:
    hosts = {route.to for route in egress.ROUTES}
    squid = (ROOT / "src/homunculus/sandbox/proxy/squid.conf").read_text()
    registries = re.search(r"^acl registries dstdomain -n (.+)$", squid, re.M)
    assert registries is not None
    assert set(registries.group(1).split()) <= hosts
    assert OPENROUTER_CHAT_URL.split("/")[2] in hosts

    security = (ROOT / "SECURITY.md").read_text()
    for route in egress.ROUTES:
        assert f"`{route.to}`" in security, route.to


def finishes_only_after_release(memory: Memory, work: Callable[[], object]) -> None:
    """Run `work` while something else holds the memory, and show it waits its turn."""
    done = threading.Event()
    with memory.lock:

        def run() -> None:
            work()
            done.set()

        thread = threading.Thread(target=run)
        thread.start()
        assert not done.wait(0.3)
    thread.join(timeout=5)
    assert done.is_set()


def test_a_save_waits_while_the_agent_is_changing_the_memory(
    client: TestClient, memory: Memory
) -> None:
    version = client.get("/api/memory/pages/profile.md").json()["version"]

    finishes_only_after_release(
        memory,
        lambda: client.put(
            "/api/memory/pages/profile.md", json={"text": "# Profile\n", "version": version}
        ),
    )


def test_an_agent_change_waits_while_a_save_is_being_recorded(
    tmp_path: Path, memory: Memory
) -> None:
    (write,) = [tool for tool in file_tools(tmp_path, memory) if tool.name == "write"]
    page = "---\ntitle: Tea\nsummary: Tea.\n---\n"

    finishes_only_after_release(
        memory, lambda: write.run({"path": "memory/wiki/tea.md", "content": page})
    )
