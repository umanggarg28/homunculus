---
name: morning-brief
description: Write a short brief for the day from the operator's goals, the to-do notes and the wiki, saved as briefs/YYYY-MM-DD.md in the workspace. Use each morning, or when the operator asks what is on today.
license: MIT
---

# Morning brief

A brief the operator can read in a minute: what matters today and what is waiting for them.

## Steps

1. The goals (`memory/goals.md`), the to-do notes (`memory/todo.md`) and the wiki index (`memory/wiki/index.md`) are already in your instructions: work from them, without reading them again. Read a wiki page only if a goal or note points to it.
2. If `briefs/` already has a brief for today, stop: say so in one line and change nothing.
3. Pick the three most useful things to do today toward the goals. Prefer work that is unblocked, has a date close by, or unblocks something else.
4. Collect every question waiting for the operator: the top of `memory/todo.md`, and any approvals you know are pending.
5. Write `briefs/YYYY-MM-DD.md`, with today's date, in the format below.
6. Reply with one line: where the brief is and the first of the three things.

## Format

```markdown
# Brief for Wednesday 25 November

## Today
1. <thing>: <why it matters now, in one sentence>
2. ...
3. ...

## Waiting for you
- <question, and what it unblocks>

## Done since the last brief
- <what was finished, from the to-do notes>
```

Leave out a section that has nothing in it. Use plain words and short sentences. Name only facts that are in memory; never guess at what the operator did.
