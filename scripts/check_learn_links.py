"""Check that every line reference in LEARN.md still points at the code it quotes.

A reference is a link to a line or range of lines, followed on the same line by the
code it refers to in backticks:

    [loop.py:62](src/homunculus/loop.py#L62) `messages = [...]`

The quoted code must appear in the linked lines, ignoring spacing. `...` stands for
any text in between. A line number written without a link is an error, because
nothing could check it. A test quoted in a python code block must match the test in
tests/ word for word, so a quoted test can never drift from the real one.

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


def outside_code_blocks(lines: list[str]) -> list[tuple[int, str, bool]]:
    """Each line as (index, text, inside a code block), skipping the fences themselves."""
    marked, inside = [], False
    for index, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            inside = not inside
            continue
        marked.append((index, line, inside))
    return marked


def link_problem(link: re.Match[str], line: str) -> str | None:
    """Why this link does not point at the code quoted after it, or None if it does."""
    path, first = ROOT / link.group(2), int(link.group(3))
    last = int(link.group(4) or first)
    if not path.is_file():
        return f"{link.group(2)} does not exist"
    lines = path.read_text().splitlines()
    if not 1 <= first <= last <= len(lines):
        return f"{link.group(2)} has no lines {first}-{last}"
    quote = CODE.search(line, link.end())
    if quote is None:
        return f"{link.group(0)} is not followed by the code it points at"
    if not quoted_code_is_there(quote.group(1), squeeze("".join(lines[first - 1 : last]))):
        return f"{link.group(2)} lines {first}-{last} do not contain `{quote.group(1)}`"
    return None


def relinked(link: re.Match[str], line: str) -> str:
    """The link pointed at its quoted code's new lines, or unchanged if that is not safe."""
    path, first = ROOT / link.group(2), int(link.group(3))
    last = int(link.group(4) or first)
    quote = CODE.search(line, link.end())
    if quote is None or not path.is_file() or link_problem(link, line) is None:
        return link.group(0)
    found = new_location(quote.group(1), path.read_text().splitlines(), last - first + 1)
    if found is None:
        return link.group(0)
    new_first, new_last = found
    lines = f"{new_first}" if new_first == new_last else f"{new_first}-{new_last}"
    anchor = f"L{new_first}" if new_first == new_last else f"L{new_first}-L{new_last}"
    # A label of the usual "file:lines" form follows the move; any other label is kept.
    old_lines = f"{first}" if first == last else f"{first}-{last}"
    label = link.group(1)
    if label == f"{Path(link.group(2)).name}:{old_lines}":
        label = f"{Path(link.group(2)).name}:{lines}"
    return f"[{label}]({link.group(2)}#{anchor})"


def fix() -> int:
    """Point moved links at their code's new lines. Returns how many were updated."""
    text = DOCUMENT.read_text().splitlines(keepends=True)
    updated = 0
    for index, line, in_code_block in outside_code_blocks(text):
        if in_code_block:
            continue

        def relink(link: re.Match[str], line: str = line) -> str:
            return relinked(link, line)

        new = LINK.sub(relink, line)
        if new != line:
            text[index] = new
            updated += 1
    if updated:
        DOCUMENT.write_text("".join(text))
    return updated


TEST_START = re.compile(r"^(async )?def (test_\w+)\(")


def quoted_tests(lines: list[str]) -> list[tuple[int, str, str]]:
    """Each test quoted in a python code block, as (line number, test name, its code)."""
    found: list[tuple[int, str, str]] = []
    block: list[str] | None = None
    start = 0
    for number, line in enumerate(lines, start=1):
        if line.strip() == "```python":
            block, start = [], number + 1
        elif line.strip() == "```" and block is not None:
            if block and (match := TEST_START.match(block[0])):
                found.append((start, match.group(2), "\n".join(block).rstrip()))
            block = None
        elif block is not None:
            block.append(line)
    return found


def test_problems(lines: list[str]) -> list[str]:
    """A quoted test that is not, word for word, a test in tests/."""
    sources = "\n".join(path.read_text() for path in sorted((ROOT / "tests").rglob("*.py")))
    return [
        f"LEARN.md:{number}: the quoted {name} does not match the test in tests/"
        for number, name, code in quoted_tests(lines)
        if code not in sources
    ]


def problems() -> list[str]:
    found: list[str] = test_problems(DOCUMENT.read_text().splitlines())
    for index, line, in_code_block in outside_code_blocks(DOCUMENT.read_text().splitlines()):
        where = f"LEARN.md:{index + 1}"
        if in_code_block:
            # A code block cannot hold a link, so it must not cite line numbers either.
            if BARE_NUMBER.search(line) or re.search(r"\bline \d+ of", line):
                found.append(f"{where}: a line number inside a code block cannot be checked")
            continue
        for link in LINK.finditer(line):
            problem = link_problem(link, line)
            if problem is not None:
                found.append(f"{where}: {problem}")
        if BARE_NUMBER.search(LINK.sub("", line)):
            found.append(f"{where}: a line number without a link: {line.strip()[:80]}")
    return found


if __name__ == "__main__":
    if "--fix" in sys.argv:
        print(f"Updated {fix()} line(s).")
    issues = problems()
    for issue in issues:
        print(issue)
    print(f"{len(issues)} problem(s)" if issues else "LEARN.md line references are correct.")
    sys.exit(1 if issues else 0)
