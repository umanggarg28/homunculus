"""The operator's identifiers: their name, emails, handles and the like, kept in a private
list in the data directory and never in the repository.

The import scan reports files that mention them, and the pre-commit check stops a commit
that would publish them. Matches are always shown masked, so a report never repeats
what it is protecting.
"""

import re
import subprocess
from pathlib import Path

FILE = "identifiers.txt"
TEMPLATE = """\
# Words that identify you, one per line: your name, emails, phone numbers, handles.
# The import scan reports files that mention them, and the pre-commit check stops a
# commit that would publish them. This file stays in your data directory, never in git.
"""


def load(home: Path) -> list[str]:
    """The identifiers, or none if the list does not exist yet."""
    path = home / FILE
    if not path.is_file():
        return []
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def ensure(home: Path) -> None:
    """Create the list with its instructions, so the operator knows where it lives."""
    path = home / FILE
    if not path.exists():
        path.write_text(TEMPLATE, encoding="utf-8")


def mask(value: str) -> str:
    """Enough of a value to recognise it, never enough to read it: r•••@e•••.com."""
    if "@" in value:
        name, _, domain = value.partition("@")
        host, _, tail = domain.rpartition(".")
        return f"{name[:1]}•••@{host[:1]}•••.{tail}" if host else f"{name[:1]}•••@•••"
    return f"{value[:1]}•••{value[-2:]}" if len(value) > 6 else "•••"


def find(text: str, identifiers: list[str]) -> list[tuple[int, str]]:
    """Each line of `text` that mentions an identifier, ignoring case: (line, identifier)."""
    found = []
    for number, line in enumerate(text.splitlines(), start=1):
        lowered = line.lower()
        for identifier in identifiers:
            if identifier.lower() in lowered:
                found.append((number, identifier))
    return found


HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def check_staged(home: Path) -> int:
    """The pre-commit check: stop a commit whose staged lines mention an identifier.
    Returns the exit code, 1 to stop the commit."""
    identifiers = load(home)
    if not identifiers:
        return 0
    # Only the lines being added are checked; -U0 leaves out the unchanged lines around them.
    diff = subprocess.run(
        ["git", "diff", "--cached", "-U0", "--no-color", "--no-ext-diff"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    hits: list[str] = []
    path = ""
    line = 0
    for row in diff.splitlines():
        if row.startswith("+++ "):
            path = row[6:] if row.startswith("+++ b/") else ""
        elif hunk := HUNK.match(row):
            line = int(hunk.group(1))
        elif row.startswith("+") and path:
            for _, identifier in find(row[1:], identifiers):
                hits.append(f"  {path}:{line}  {mask(identifier)}")
            line += 1
    if hits:
        print("Commit stopped: these staged lines mention one of your identifiers.")
        print("\n".join(hits))
        print(f"Remove them, or edit {home / FILE} if one should not be on the list.")
        return 1
    return 0
