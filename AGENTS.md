# Agent instructions

## Writing rules

These are checked in every code review.

- Comments explain why, not what.
- Comments stay true over time. They carry no incident stories.
- No names, casual tone or internal back-and-forth.
- No AI co-author lines on commits or PRs: no `Co-Authored-By` trailer and no "Generated with" footer.

## Learning document

LEARN.md teaches the code and is updated in the same pull request as every change it describes, in every part: traces, tables, tests quoted and exercises. A reference to a line of code is a link to that line followed by the code in backticks, for example ``[loop.py:62](src/homunculus/loop.py#L62) `messages = [...]` ``. `uv run python scripts/check_learn_links.py` checks every such reference and runs on every pull request; a line number without a link is an error.

## Agent skills

### Issue tracker

Issues and specs live in this repo's GitHub Issues, managed with the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five default triage labels, each named after its role. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
