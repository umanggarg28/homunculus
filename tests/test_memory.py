"""The wiki: the agent's memory, exercised through the loop with a scripted model."""

import json
import re
import subprocess
from pathlib import Path

import pytest
from scripted_model import ScriptedModel

from homunculus import loop
from homunculus.activity import Activity
from homunculus.approvals import Approvals
from homunculus.memory import Memory
from homunculus.model import Message, ToolCall
from homunculus.tools.files import file_tools

TEA = """---
title: Tea
summary: What tea the operator drinks, and when.
---

The operator drinks green tea in the morning.[^1]

[^1]: raw/conversations/2026-11-14.md
"""


@pytest.fixture
def home(tmp_path: Path) -> Path:
    folder = tmp_path / "home"
    (folder / "workspace").mkdir(parents=True)
    return folder


@pytest.fixture
def memory(home: Path) -> Memory:
    opened = Memory.open(home / "memory")
    # A conversation the agent can cite, saved as a raw source.
    source = opened.root / "raw" / "conversations" / "2026-11-14.md"
    source.parent.mkdir(parents=True)
    source.write_text("I drink green tea every morning.\n")
    # Recorded as the operator's own, as an import will be, so tests start from a clean state.
    opened.take_hand_edits()
    return opened


def tool_call(name: str, **arguments: object) -> ToolCall:
    return ToolCall(id=f"call_{name}", name=name, arguments=json.dumps(arguments))


async def run_job(model: ScriptedModel, home: Path, memory: Memory, text: str) -> None:
    tools = file_tools(home / "workspace", memory)
    approvals = Approvals(home / "approvals.json")
    history: list[Message] = []
    _ = [
        item
        async for item in loop.run(
            model, tools, Activity(home), approvals, history, text, memory=memory
        )
    ]


def tool_result(model: ScriptedModel, call: int = 1) -> str:
    last = model.received[call][-1]
    assert last["role"] == "tool"
    return str(last["content"])


def commits(memory: Memory) -> list[tuple[str, str]]:
    """Each commit as (author, subject), newest first."""
    out = subprocess.run(
        ["git", "log", "--format=%an\t%s"],
        cwd=memory.root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [tuple(line.split("\t", 1)) for line in out.splitlines()]  # type: ignore[misc]


def write_page(path: str, content: str) -> list[list[str | ToolCall]]:
    return [[tool_call("write", path=path, content=content)], ["Done."]]


# Starting state


def test_memory_starts_empty_as_a_git_repository_with_its_own_files(home: Path) -> None:
    memory = Memory.open(home / "memory")

    assert (memory.root / ".git").is_dir()
    for name in ["profile.md", "goals.md", "SCHEMA.md", "wiki/index.md", "wiki/log.md"]:
        assert (memory.root / name).is_file(), name
    assert [author for author, _ in commits(memory)] == ["Homunculus"]


def test_opening_memory_again_keeps_what_is_there(home: Path) -> None:
    Memory.open(home / "memory")
    (home / "memory" / "goals.md").write_text("# Goals\n\nFinish the book.\n")

    Memory.open(home / "memory")

    assert "Finish the book." in (home / "memory" / "goals.md").read_text()


# The prompt


async def test_every_turn_carries_the_profile_goals_and_index_with_their_budgets(
    home: Path, memory: Memory
) -> None:
    (memory.root / "goals.md").write_text("# Goals\n\nFinish the book by March.\n")
    model = ScriptedModel([["Hello."]])

    await run_job(model, home, memory, "Hi")

    system = str(model.received[0][0]["content"])
    assert "Finish the book by March." in system
    assert re.search(r"Profile \(\d+ of 2,000 characters\)", system)
    assert re.search(r"Goals \(\d+ of 2,000 characters\)", system)
    assert re.search(r"Wiki index \(\d+ of 6,000 characters\)", system)


# Writing pages


async def test_a_sourced_page_is_written_indexed_logged_and_committed(
    home: Path, memory: Memory
) -> None:
    model = ScriptedModel(write_page("memory/wiki/tea.md", TEA))

    await run_job(model, home, memory, "Remember my tea.")

    assert (memory.root / "wiki" / "tea.md").read_text() == TEA
    index = (memory.root / "wiki" / "index.md").read_text()
    assert "- [Tea](tea.md): What tea the operator drinks, and when." in index
    log = (memory.root / "wiki" / "log.md").read_text()
    assert re.search(r"^## \[\d{4}-\d\d-\d\d \d\d:\d\d\] write \| wiki/tea\.md$", log, re.M)
    assert commits(memory)[0] == ("Homunculus", "write wiki/tea.md")


async def test_the_next_turn_sees_the_new_page_in_its_index(home: Path, memory: Memory) -> None:
    model = ScriptedModel(write_page("memory/wiki/tea.md", TEA))
    await run_job(model, home, memory, "Remember my tea.")
    later = ScriptedModel([["You drink green tea."]])

    await run_job(later, home, memory, "What do I drink?")

    # In the prompt the index names each page by the path the file tools take.
    assert "[Tea](memory/wiki/tea.md)" in str(later.received[0][0]["content"])


async def test_a_fact_with_no_source_is_refused(home: Path, memory: Memory) -> None:
    unsourced = TEA.replace("in the morning.[^1]", "in the morning.")
    model = ScriptedModel(write_page("memory/wiki/tea.md", unsourced))

    await run_job(model, home, memory, "Remember my tea.")

    assert "has no source" in tool_result(model)
    assert not (memory.root / "wiki" / "tea.md").exists()


async def test_a_source_must_be_a_raw_file_that_exists(home: Path, memory: Memory) -> None:
    missing = TEA.replace("raw/conversations/2026-11-14.md", "raw/conversations/2020-01-01.md")
    model = ScriptedModel(write_page("memory/wiki/tea.md", missing))

    await run_job(model, home, memory, "Remember my tea.")

    assert "raw/conversations/2020-01-01.md does not exist" in tool_result(model)
    assert not (memory.root / "wiki" / "tea.md").exists()


async def test_a_link_to_a_page_that_does_not_exist_is_refused(home: Path, memory: Memory) -> None:
    linked = TEA + "\n## See also\n\n- [Coffee](coffee.md)\n"
    model = ScriptedModel(write_page("memory/wiki/tea.md", linked))

    await run_job(model, home, memory, "Remember my tea.")

    assert "links to coffee.md, which does not exist" in tool_result(model)
    assert not (memory.root / "wiki" / "tea.md").exists()


async def test_a_page_without_a_title_and_summary_is_refused(home: Path, memory: Memory) -> None:
    bare = TEA.split("---\n", 2)[2]
    model = ScriptedModel(write_page("memory/wiki/tea.md", bare))

    await run_job(model, home, memory, "Remember my tea.")

    assert "needs a title and a summary" in tool_result(model)


async def test_a_new_page_nothing_links_to_is_written_with_a_note(
    home: Path, memory: Memory
) -> None:
    model = ScriptedModel(write_page("memory/wiki/tea.md", TEA))

    await run_job(model, home, memory, "Remember my tea.")

    assert "No other page links to wiki/tea.md yet." in tool_result(model)
    assert (memory.root / "wiki" / "tea.md").exists()


# The profile and its cap


async def test_a_sourced_profile_line_is_kept(home: Path, memory: Memory) -> None:
    profile = (
        "# Profile\n\nDrinks green tea in the morning.[^1]\n\n## How I like to work\n\n"
        "[^1]: raw/conversations/2026-11-14.md\n"
    )
    model = ScriptedModel(write_page("memory/profile.md", profile))

    await run_job(model, home, memory, "Add tea to my profile.")

    assert (memory.root / "profile.md").read_text() == profile


async def test_the_profile_holds_no_inferred_facts(home: Path, memory: Memory) -> None:
    profile = (
        "# Profile\n\n## Inferred\n\nPrefers mornings.[^1]\n\n"
        "[^1]: raw/conversations/2026-11-14.md\n"
    )
    model = ScriptedModel(write_page("memory/profile.md", profile))

    await run_job(model, home, memory, "Add a guess to my profile.")

    assert "The profile holds only stated facts" in tool_result(model)


async def test_a_profile_over_its_cap_is_refused(home: Path, memory: Memory) -> None:
    line = "A sourced fact about the operator.[^1]\n"
    profile = "# Profile\n\n" + line * 80 + "\n[^1]: raw/conversations/2026-11-14.md\n"
    model = ScriptedModel(write_page("memory/profile.md", profile))

    await run_job(model, home, memory, "Fill my profile.")

    assert "over its cap of 2,000 characters" in tool_result(model)
    assert "A sourced fact" not in (memory.root / "profile.md").read_text()


# What only code, or only the operator, may change


@pytest.mark.parametrize(
    "path", ["memory/raw/conversations/2026-11-14.md", "memory/wiki/index.md", "memory/wiki/log.md"]
)
async def test_raw_sources_and_generated_files_cannot_be_written(
    home: Path, memory: Memory, path: str
) -> None:
    before = (memory.root / path.removeprefix("memory/")).read_text()
    model = ScriptedModel(write_page(path, "forged\n"))

    await run_job(model, home, memory, "Change it.")

    assert tool_result(model) == "Not done: the gate answered 'refuse' for this action."
    assert (memory.root / path.removeprefix("memory/")).read_text() == before


async def test_changing_the_goals_waits_for_the_operator(home: Path, memory: Memory) -> None:
    before = (memory.root / "goals.md").read_text()
    model = ScriptedModel(write_page("memory/goals.md", "# Goals\n\nLearn Spanish.\n"))

    await run_job(model, home, memory, "Add a goal.")

    assert "waiting for the operator" in tool_result(model).lower()
    assert (memory.root / "goals.md").read_text() == before


async def test_a_path_out_of_memory_is_refused(home: Path, memory: Memory) -> None:
    model = ScriptedModel(write_page("memory/../escaped.md", "escaped\n"))

    await run_job(model, home, memory, "Escape.")

    assert tool_result(model) == "Not done: the gate answered 'refuse' for this action."
    assert not (home / "escaped.md").exists()


# Hand edits


async def test_a_hand_edit_is_committed_as_the_operators_before_the_agent_writes(
    home: Path, memory: Memory
) -> None:
    await run_job(ScriptedModel(write_page("memory/wiki/tea.md", TEA)), home, memory, "Save.")
    page = memory.root / "wiki" / "tea.md"
    page.write_text(TEA.replace("green tea", "green tea with honey"))

    await run_job(ScriptedModel([["Noted."]]), home, memory, "Hi")
    await run_job(
        ScriptedModel([[tool_call("read", path="memory/wiki/tea.md")], ["Read."]]),
        home,
        memory,
        "Read it.",
    )

    assert ("Operator", "Hand edit: wiki/tea.md") in commits(memory)
    assert re.search(r"hand edit \| wiki/tea\.md", (memory.root / "wiki" / "log.md").read_text())


async def test_a_hand_edited_page_is_never_rewritten_whole(home: Path, memory: Memory) -> None:
    await run_job(ScriptedModel(write_page("memory/wiki/tea.md", TEA)), home, memory, "Save.")
    honey = TEA.replace("green tea", "green tea with honey")
    (memory.root / "wiki" / "tea.md").write_text(honey)
    model = ScriptedModel(write_page("memory/wiki/tea.md", TEA))

    await run_job(model, home, memory, "Rewrite it.")

    assert "you edited it by hand" in tool_result(model)
    assert (memory.root / "wiki" / "tea.md").read_text() == honey


async def test_a_hand_edited_page_can_still_be_added_to(home: Path, memory: Memory) -> None:
    await run_job(ScriptedModel(write_page("memory/wiki/tea.md", TEA)), home, memory, "Save.")
    honey = TEA.replace("green tea", "green tea with honey")
    (memory.root / "wiki" / "tea.md").write_text(honey)
    addition = "The operator drinks green tea with honey in the morning.[^1]\nNo sugar.[^1]"
    model = ScriptedModel(
        [
            [
                tool_call(
                    "edit",
                    path="memory/wiki/tea.md",
                    old="The operator drinks green tea with honey in the morning.[^1]",
                    new=addition,
                )
            ],
            ["Added."],
        ]
    )

    await run_job(model, home, memory, "Add that I take no sugar.")

    assert "No sugar.[^1]" in (memory.root / "wiki" / "tea.md").read_text()


async def test_the_operators_own_unsourced_lines_do_not_block_the_agent(
    home: Path, memory: Memory
) -> None:
    await run_job(ScriptedModel(write_page("memory/wiki/tea.md", TEA)), home, memory, "Save.")
    (memory.root / "wiki" / "tea.md").write_text(TEA + "\nMy own note, no source needed.\n")
    model = ScriptedModel(
        [
            [
                tool_call(
                    "edit",
                    path="memory/wiki/tea.md",
                    old="in the morning.[^1]",
                    new="in the morning, before work.[^1]",
                )
            ],
            ["Edited."],
        ]
    )

    await run_job(model, home, memory, "Add that it is before work.")

    assert "before work" in (memory.root / "wiki" / "tea.md").read_text()
