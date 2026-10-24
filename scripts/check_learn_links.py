"""Check that every line reference in LEARN.md still points at the code it quotes.

A reference is a link to a line or range of lines, followed on the same line by the
code it refers to in backticks:

    [loop.py:62](src/homunculus/loop.py#L62) `messages = [...]`

The quoted code must appear in the linked lines, ignoring spacing. `...` stands for
any text in between. A line number written without a link is an error, because
nothing could check it.

Run: uv run python scripts/check_learn_links.py
     uv run python scripts/check_learn_links.py --fix

With --fix, a link whose quoted code has moved is pointed at its new lines, but only
when the code now appears in exactly one place. Anything else is left for a person.
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


def new_location(quote: str, lines: list[str], span: int) -> tuple[int, int] | None:
    """The only run of `span` lines containing the quote, as (first, last), or None."""
    matches = [
        (start + 1, start + span)
        for start in range(len(lines) - span + 1)
        if quoted_code_is_there(quote, squeeze("".join(lines[start : start + span])))
    ]
    return matches[0] if len(matches) == 1 else None


def fix() -> int:
    """Point moved links at their code's new lines. Returns how many were updated."""
    text = DOCUMENT.read_text().splitlines(keepends=True)
    updated = 0
    for index, line in enumerate(text):

        def relink(link: re.Match[str], line: str = line) -> str:
            nonlocal updated
            path, first = ROOT / link.group(2), int(link.group(3))
            last = int(link.group(4) or first)
            quote = CODE.search(line, link.end())
            if quote is None or not path.is_file():
                return link.group(0)
            lines = path.read_text().splitlines()
            if 1 <= first <= last <= len(lines) and quoted_code_is_there(
                quote.group(1), squeeze("".join(lines[first - 1 : last]))
            ):
                return link.group(0)
            found = new_location(quote.group(1), lines, last - first + 1)
            if found is None:
                return link.group(0)
            updated += 1
            new_first, new_last = found
            name = Path(link.group(2)).name
            if new_first == new_last:
                return f"[{name}:{new_first}]({link.group(2)}#L{new_first})"
            return f"[{name}:{new_first}-{new_last}]({link.group(2)}#L{new_first}-L{new_last})"

        text[index] = LINK.sub(relink, line)
    DOCUMENT.write_text("".join(text))
    return updated


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
    if "--fix" in sys.argv:
        print(f"Updated {fix()} link(s).")
    issues = problems()
    for issue in issues:
        print(issue)
    print(f"{len(issues)} problem(s)" if issues else "LEARN.md line references are correct.")
    sys.exit(1 if issues else 0)
