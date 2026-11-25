"""Skills: folders in the Agent Skills format, from the repository and the data directory.

The catalogue is checked in the prompt the model receives, and skills are read with the
agent's own file tools, through the loop with a scripted model; only the model is faked.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from scripted_model import Reply, ScriptedModel

from homunculus import loop
from homunculus.activity import Activity
from homunculus.approvals import Approvals
from homunculus.model import ToolCall
from homunculus.skills import Skills
from homunculus.tools.files import file_tools


def make_skill(root: Path, folder: str, frontmatter: str, body: str = "Do the thing.\n") -> Path:
    path = root / folder
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(f"---\n{frontmatter}\n---\n{body}")
    return path


def brief(root: Path, description: str = "Write a short brief for the day.") -> Path:
    return make_skill(
        root,
        "morning-brief",
        f"name: morning-brief\ndescription: {description}",
        "1. Read memory/goals.md.\n2. Write briefs/today.md.\n",
    )


@pytest.fixture
def places(tmp_path: Path) -> tuple[Path, Path]:
    builtin, personal = tmp_path / "repo-skills", tmp_path / "home" / "skills"
    builtin.mkdir()
    personal.mkdir(parents=True)
    return builtin, personal


def skills_in(places: tuple[Path, Path], identifiers: list[str] | None = None) -> Skills:
    builtin, personal = places
    return Skills(builtin, personal, identifiers=lambda: identifiers or [])


async def system_prompt_seen(tmp_path: Path, skills: Skills, model: ScriptedModel) -> str:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    tools = file_tools(workspace, skills=skills)
    activity, approvals = Activity(tmp_path), Approvals(tmp_path / "approvals.json")
    async for _ in loop.run(model, tools, activity, approvals, [], "Hello", skills=skills):
        pass
    return str(model.received[-1][0]["content"])


def read_call(path: str) -> ToolCall:
    return ToolCall(id="r", name="read", arguments=json.dumps({"path": path}))


def tool_results(model: ScriptedModel) -> list[str]:
    return [str(m["content"]) for m in model.received[-1] if m["role"] == "tool"]


# The catalogue


async def test_every_skill_is_named_and_described_in_the_prompt(
    tmp_path: Path, places: tuple[Path, Path]
) -> None:
    builtin, personal = places
    brief(builtin)
    make_skill(personal, "plan-week", "name: plan-week\ndescription: Plan the week ahead.")
    model = ScriptedModel([["Hi."]])

    prompt = await system_prompt_seen(tmp_path, skills_in(places), model)

    assert "<name>morning-brief</name>" in prompt
    assert "<description>Write a short brief for the day.</description>" in prompt
    assert "<location>skills/morning-brief/SKILL.md</location>" in prompt
    assert "<name>plan-week</name>" in prompt
    # Only the catalogue: the instructions themselves wait until the skill is read.
    assert "Write briefs/today.md" not in prompt


async def test_the_catalogue_is_built_from_the_folders_each_time(
    tmp_path: Path, places: tuple[Path, Path]
) -> None:
    builtin, personal = places
    skills = skills_in(places)
    model = ScriptedModel([["Hi."], ["Hi again."]])

    before = await system_prompt_seen(tmp_path, skills, model)
    brief(personal)
    after = await system_prompt_seen(tmp_path, skills, model)

    assert "morning-brief" not in before
    assert "<name>morning-brief</name>" in after


async def test_with_no_skills_the_prompt_says_nothing_about_them(
    tmp_path: Path, places: tuple[Path, Path]
) -> None:
    model = ScriptedModel([["Hi."]])

    prompt = await system_prompt_seen(tmp_path, skills_in(places), model)

    assert "available_skills" not in prompt
    assert "skills/" not in prompt


async def test_a_description_is_written_so_it_cannot_break_the_catalogue(
    tmp_path: Path, places: tuple[Path, Path]
) -> None:
    builtin, _ = places
    brief(builtin, description="'Use when <brief> & such.'")
    model = ScriptedModel([["Hi."]])

    prompt = await system_prompt_seen(tmp_path, skills_in(places), model)

    assert "<description>Use when &lt;brief&gt; &amp; such.</description>" in prompt


# Reading a skill


async def test_the_agent_reads_a_skills_instructions_and_its_files_with_the_read_tool(
    tmp_path: Path, places: tuple[Path, Path]
) -> None:
    builtin, _ = places
    folder = brief(builtin)
    (folder / "references").mkdir()
    (folder / "references" / "format.md").write_text("Three items, most useful first.\n")
    calls: Reply = [
        read_call("skills/morning-brief/SKILL.md"),
        read_call("skills/morning-brief/references/format.md"),
    ]
    model = ScriptedModel([calls, ["Done."]])

    await system_prompt_seen(tmp_path, skills_in(places), model)

    instructions, reference = tool_results(model)
    assert "3\tdescription: Write a short brief for the day." in instructions
    assert "6\t2. Write briefs/today.md." in instructions
    assert reference == "1\tThree items, most useful first."


def test_listing_skills_shows_each_skill_folder(places: tuple[Path, Path], tmp_path: Path) -> None:
    builtin, personal = places
    brief(builtin)
    make_skill(personal, "plan-week", "name: plan-week\ndescription: Plan the week ahead.")
    tools = {t.name: t for t in file_tools(tmp_path, skills=skills_in(places))}

    listed = tools["list"].call({"path": "skills"}).content[0].text  # type: ignore[union-attr]

    assert listed == "morning-brief/\nplan-week/"


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("write", {"path": "skills/morning-brief/SKILL.md", "content": "Changed."}),
        ("edit", {"path": "skills/morning-brief/SKILL.md", "old": "Read", "new": "Skip"}),
        ("delete", {"path": "skills/morning-brief/SKILL.md"}),
        ("rename", {"path": "skills/morning-brief/SKILL.md", "to": "elsewhere.md"}),
        ("write", {"path": "skills/new-one/SKILL.md", "content": "A new skill."}),
    ],
)
def test_skills_cannot_be_changed_with_the_file_tools(
    places: tuple[Path, Path], tmp_path: Path, name: str, arguments: dict[str, Any]
) -> None:
    builtin, _ = places
    folder = brief(builtin)
    before = (folder / "SKILL.md").read_text()
    tools = {t.name: t for t in file_tools(tmp_path, skills=skills_in(places))}

    result = tools[name].call(arguments)

    assert result.is_error
    assert "read-only" in result.content[0].text  # type: ignore[union-attr]
    assert (folder / "SKILL.md").read_text() == before
    assert not (builtin / "new-one").exists()


def test_a_link_inside_a_skill_is_not_followed(places: tuple[Path, Path], tmp_path: Path) -> None:
    builtin, _ = places
    folder = brief(builtin)
    secret = tmp_path / "secret.txt"
    secret.write_text("private")
    (folder / "notes.md").symlink_to(secret)
    tools = {t.name: t for t in file_tools(tmp_path, skills=skills_in(places))}

    result = tools["read"].call({"path": "skills/morning-brief/notes.md"})

    assert result.is_error
    assert "private" not in result.content[0].text  # type: ignore[union-attr]


# Finding skills


def test_a_personal_skill_replaces_a_built_in_one_of_the_same_name_with_a_warning(
    places: tuple[Path, Path],
) -> None:
    builtin, personal = places
    brief(builtin)
    brief(personal, description="My own way of writing the brief.")
    skills = skills_in(places)

    found = skills.found()

    assert found["morning-brief"].description == "My own way of writing the brief."
    assert found["morning-brief"].folder == (personal / "morning-brief").resolve()
    assert skills.problems() == [
        "Your skill morning-brief is used in place of the built-in one of the same name."
    ]


def test_a_skill_whose_name_differs_from_its_folder_still_loads_with_a_warning(
    places: tuple[Path, Path],
) -> None:
    builtin, _ = places
    make_skill(builtin, "weekly-review", "name: Weekly Review\ndescription: Review the week.")
    skills = skills_in(places)

    assert list(skills.found()) == ["weekly-review"]
    assert skills.problems() == [
        "Skill weekly-review: its name, 'Weekly Review', should match its folder's name."
    ]


@pytest.mark.parametrize(
    ("folder", "frontmatter", "problem"),
    [
        ("no-description", "name: no-description", "it has no description"),
        ("bad-yaml", "name: bad-yaml\ndescription: [unclosed", "its frontmatter is not valid YAML"),
    ],
)
def test_a_skill_that_cannot_be_described_is_left_out_and_reported(
    places: tuple[Path, Path], folder: str, frontmatter: str, problem: str
) -> None:
    builtin, _ = places
    make_skill(builtin, folder, frontmatter)
    skills = skills_in(places)

    assert skills.found() == {}
    assert skills.problems() == [f"Skill {folder} is left out: {problem}."]


def test_a_file_without_frontmatter_is_not_a_skill(places: tuple[Path, Path]) -> None:
    builtin, _ = places
    (builtin / "notes").mkdir()
    (builtin / "notes" / "SKILL.md").write_text("Just some notes.\n")
    skills = skills_in(places)

    assert skills.found() == {}
    assert skills.problems() == ["Skill notes is left out: SKILL.md does not start with ---."]


def test_a_skill_folder_that_is_a_link_is_not_used(
    places: tuple[Path, Path], tmp_path: Path
) -> None:
    _, personal = places
    elsewhere = brief(tmp_path / "elsewhere")
    (personal / "morning-brief").symlink_to(elsewhere)

    assert skills_in(places).found() == {}


def test_a_personal_skill_that_mentions_an_identifier_is_left_out(
    places: tuple[Path, Path],
) -> None:
    _, personal = places
    folder = brief(personal)
    (folder / "references").mkdir()
    (folder / "references" / "people.md").write_text("Send it to\nasha.rao@example.com\n")
    skills = skills_in(places, identifiers=["asha.rao@example.com"])

    assert skills.found() == {}
    assert skills.problems() == [
        "Your skill morning-brief is left out: references/people.md line 2 mentions one of "
        "your identifiers (a•••@e•••.com). Skills hold instructions, never facts about you."
    ]


def test_the_instructions_of_a_skill_are_its_body(places: tuple[Path, Path]) -> None:
    builtin, _ = places
    brief(builtin)

    assert skills_in(places).instructions("morning-brief") == (
        "1. Read memory/goals.md.\n2. Write briefs/today.md."
    )
    assert skills_in(places).instructions("not-a-skill") is None


def test_every_repository_skill_follows_the_specification() -> None:
    """The same check runs in CI with the reference validator, skills-ref."""
    from skills_ref import validate

    repository = Path(__file__).resolve().parents[1] / "skills"
    folders = [p for p in repository.iterdir() if p.is_dir()]

    assert folders
    assert {folder.name: validate(folder) for folder in folders} == {f.name: [] for f in folders}
