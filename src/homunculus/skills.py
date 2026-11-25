"""Skills: instructions for particular kinds of task, kept as folders in the Agent Skills
format (https://agentskills.io/specification).

A skill is a folder holding `SKILL.md`: YAML frontmatter with a `name` and a
`description`, then the instructions in Markdown, with optional `scripts/` and
`references/` beside it. The agent sees skills in three steps, each only when needed:
the name and description of every skill are in its prompt; it reads a skill's
`SKILL.md` with the read tool when a task fits it; and it reads or runs the skill's
other files only when the instructions call for them. A routine skips the choice and
hands the agent its skill's instructions directly.

Generic skills ship in the repository's `skills/` folder. Personal skills live in the
data directory's `skills/` folder, are never published, and replace a built-in skill of
the same name. Skills are read from disk each time they are needed, so a change to a
folder shows on the next turn.

Checking follows the standard's advice for clients: lenient here, so a skill written for
another tool still loads with a warning, and strict in the pull request checks, where
the reference validator runs on every repository skill.
"""

import html
import os
import re
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import strictyaml

from homunculus import identifiers

SKILL_FILE = "SKILL.md"
# The standard's limit. Longer descriptions are cut, so one skill cannot crowd the prompt.
MAX_DESCRIPTION = 1024
# The standard's rule for names; a folder named otherwise still loads, with a warning.
GOOD_NAME = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
# Folder names become paths in the file tools and mount points in the sandbox, so
# anything that could change the meaning of either is refused.
SAFE_FOLDER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
# Personal skill files larger than this are not scanned for identifiers, and so are left
# out: a skill holds instructions, which are small.
MAX_SCANNED_BYTES = 1024 * 1024

CATALOGUE_INTRO = """\
# Skills

Skills are instructions for particular kinds of task. When a task matches a skill's \
description, read its SKILL.md with the read tool before you start, and follow it. \
Paths in a skill are relative to its folder, skills/<name>/. In the shell, that folder \
is /skills/<name>. Skills are read-only."""


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    # The skill's folder, with no link anywhere in it resolved away.
    folder: Path
    personal: bool


class Skills:
    def __init__(
        self,
        builtin: Path,
        personal: Path,
        identifiers: Callable[[], list[str]] = lambda: [],
    ) -> None:
        self._builtin = builtin
        self._personal = personal
        self._identifiers = identifiers
        self._problems: list[str] = []

    def found(self) -> dict[str, Skill]:
        """Every usable skill by name, read from the folders now. What was wrong with
        any folder is kept for `problems`."""
        problems: list[str] = []
        skills = self._scan(self._builtin, personal=False, problems=problems)
        mentions = self._identifiers()
        for name, skill in self._scan(self._personal, personal=True, problems=problems).items():
            mention = _mention(skill.folder, mentions) if mentions else None
            if mention is not None:
                problems.append(
                    f"Your skill {name} is left out: {mention}. Skills hold instructions, "
                    "never facts about you."
                )
                continue
            if name in skills:
                problems.append(
                    f"Your skill {name} is used in place of the built-in one of the same name."
                )
            skills[name] = skill
        self._problems = problems
        return dict(sorted(skills.items()))

    def problems(self) -> list[str]:
        """What was wrong with the skill folders when they were last read."""
        return list(self._problems)

    def get(self, name: str) -> Skill | None:
        return self.found().get(name)

    def catalogue(self) -> str:
        """The prompt's list of skills, or nothing when there are none, so the model is
        never told about skills it does not have."""
        found = self.found()
        if not found:
            return ""
        entries = "\n".join(
            "  <skill>\n"
            f"    <name>{html.escape(skill.name)}</name>\n"
            f"    <description>{html.escape(skill.description)}</description>\n"
            f"    <location>skills/{html.escape(skill.name)}/{SKILL_FILE}</location>\n"
            "  </skill>"
            for skill in found.values()
        )
        return f"{CATALOGUE_INTRO}\n\n<available_skills>\n{entries}\n</available_skills>"

    def instructions(self, name: str) -> str | None:
        """A skill's instructions, the Markdown after its frontmatter, or None if there
        is no such skill."""
        skill = self.get(name)
        if skill is None:
            return None
        text = _read_plain(skill.folder / SKILL_FILE)
        return _split(text)[1] if text is not None else None

    def _scan(self, root: Path, personal: bool, problems: list[str]) -> dict[str, Skill]:
        found: dict[str, Skill] = {}
        if not root.is_dir():
            return found
        whose = "Your skill" if personal else "Skill"
        for entry in sorted(os.scandir(root), key=lambda e: e.name):
            # A link could lead the sandbox's read-only mount anywhere on this machine.
            if entry.name.startswith(".") or not entry.is_dir(follow_symlinks=False):
                continue
            if not SAFE_FOLDER.fullmatch(entry.name):
                problems.append(f"{whose} folder {entry.name!r} is left out: rename it.")
                continue
            text = _read_plain(Path(entry.path) / SKILL_FILE)
            if text is None:
                continue
            try:
                name, description = _describe(text)
            except ValueError as error:
                problems.append(f"{whose} {entry.name} is left out: {error}.")
                continue
            if name != entry.name or not GOOD_NAME.fullmatch(name):
                problems.append(
                    f"{whose} {entry.name}: its name, {name!r}, should match its folder's name."
                )
            found[entry.name] = Skill(
                entry.name, description[:MAX_DESCRIPTION], Path(entry.path).resolve(), personal
            )
        return found


def _describe(text: str) -> tuple[str, str]:
    """A skill's name and description, from the frontmatter of its SKILL.md."""
    frontmatter, _ = _split(text)
    try:
        data = strictyaml.load(frontmatter).data
    except strictyaml.YAMLError:
        raise ValueError("its frontmatter is not valid YAML") from None
    if not isinstance(data, dict):
        raise ValueError("its frontmatter is not valid YAML")
    description = data.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("it has no description")
    name = data.get("name")
    return (name.strip() if isinstance(name, str) else ""), " ".join(description.split())


def _split(text: str) -> tuple[str, str]:
    """The frontmatter and the body of a SKILL.md. The frontmatter ends at the first line
    that is only `---`, so a `---` inside a value does not end it."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"{SKILL_FILE} does not start with ---")
    for number, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[1:number]), "\n".join(lines[number + 1 :]).strip()
    raise ValueError(f"{SKILL_FILE} has no closing ---")


def _read_plain(path: Path) -> str | None:
    """A regular file's text, or None if it is missing, a link or not text."""
    try:
        handle = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    with os.fdopen(handle, "rb") as file:
        found = os.fstat(file.fileno())
        if not stat.S_ISREG(found.st_mode) or found.st_size > MAX_SCANNED_BYTES:
            return None
        try:
            return file.read().decode()
        except UnicodeDecodeError:
            return None


def _mention(folder: Path, wanted: list[str]) -> str | None:
    """Where a skill's files first mention one of the operator's identifiers, masked, or
    None. A file that cannot be read as text counts as a mention, since it cannot be
    checked."""
    for here, folders, names in os.walk(folder):
        folders.sort()
        for name in sorted(names):
            path = Path(here, name)
            relative = path.relative_to(folder).as_posix()
            if path.is_symlink():
                continue  # never followed: not read by the tools, not mounted
            text = _read_plain(path)
            if text is None:
                return f"{relative} could not be checked for your identifiers"
            hits = identifiers.find(text, wanted)
            if hits:
                line, identifier = hits[0]
                return (
                    f"{relative} line {line} mentions one of your identifiers "
                    f"({identifiers.mask(identifier)})"
                )
    return None
