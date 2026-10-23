"""Check that every line reference in LEARN.md still points at the code it quotes.

A reference is a link to a line or range of lines, followed on the same line by the
code it refers to in backticks:

    [loop.py:62](src/homunculus/loop.py#L62) `messages = [...]`

The quoted code must appear in the linked lines, ignoring spacing. `...` stands for
any text in between. A line number written without a link is an error, because
nothing could check it.

Run: uv run python scripts/check_learn_links.py
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCUMENT = ROOT / "LEARN.md"

LINK = re.compile(r"\[([^\]]+)\]\(([^)#\s]+)#L(\d+)(?:-L(\d+))?\)")
CODE = re.compile(r"`([^`]+)`")
BARE_NUMBER = re.compile(r"\b[Ll]ines? \d+")


def squeeze(text: str) -> str:
    return re.sub(r"\s+", "", text)


def quoted_code_is_there(quote: str, cited: str) -> bool:
    """True when the quote's pieces, split at `...`, appear in order in the cited code."""
    position = 0
    for piece in re.split(r"\.\.\.|…", squeeze(quote)):
        if not piece:
            continue
        found = cited.find(piece, position)
        if found < 0:
            return False
        position = found + len(piece)
    return True


def problems() -> list[str]:
    found: list[str] = []
    in_code_block = False
    for number, line in enumerate(DOCUMENT.read_text().splitlines(), start=1):
        if line.lstrip().startswith("```"):
            in_code_block = not in_code_block
            continue
        where = f"LEARN.md:{number}"
        if in_code_block:
            # A code block cannot hold a link, so it must not cite line numbers either.
            if BARE_NUMBER.search(line) or re.search(r"\bline \d+ of", line):
                found.append(f"{where}: a line number inside a code block cannot be checked")
            continue
        for link in LINK.finditer(line):
            path, first = ROOT / link.group(2), int(link.group(3))
            last = int(link.group(4) or first)
            if not path.is_file():
                found.append(f"{where}: {link.group(2)} does not exist")
                continue
            lines = path.read_text().splitlines()
            if not 1 <= first <= last <= len(lines):
                found.append(f"{where}: {link.group(2)} has no lines {first}-{last}")
                continue
            quote = CODE.search(line, link.end())
            if quote is None:
                found.append(f"{where}: {link.group(0)} is not followed by the code it points at")
                continue
            cited = squeeze("".join(lines[first - 1 : last]))
            if not quoted_code_is_there(quote.group(1), cited):
                found.append(
                    f"{where}: {link.group(2)} lines {first}-{last} do not contain "
                    f"`{quote.group(1)}`"
                )
        without_links = LINK.sub("", line)
        if BARE_NUMBER.search(without_links):
            found.append(f"{where}: a line number without a link: {line.strip()[:80]}")
    return found


if __name__ == "__main__":
    issues = problems()
    for issue in issues:
        print(issue)
    print(f"{len(issues)} problem(s)" if issues else "LEARN.md line references are correct.")
    sys.exit(1 if issues else 0)
