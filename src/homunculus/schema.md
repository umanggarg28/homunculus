# Schema

How this wiki is organised. The operator owns this file; changes to it are approved by the operator.

## Folders

- `raw/` holds sources: imported files and saved conversations. Read them; never change them.
- `wiki/` holds the pages you write. `wiki/index.md` and `wiki/log.md` are kept by the program; never write them.
- `profile.md` is short and always in your prompt. It holds stated facts only, and a "How I like to work" section.
- `goals.md` holds the operator's goals. Suggest changes; the operator approves them.

## A page

```
---
title: The book
summary: The operator's novel, its draft and its deadline.
---

The draft stands at 38,200 of 80,000 words.[^1]

## Inferred

Chapter 7 is the book's midpoint.[^2]

## See also

- [Chapter 7](book/chapter-7.md)

[^1]: raw/conversations/2026-11-13.md
[^2]: raw/book-notes/structure.md
```

- Every page starts with a title and a one-line summary. The index is built from them.
- Every fact ends with a footnote naming the raw file it came from.
- What the operator said goes in the body. What you inferred goes under `## Inferred`, and stays out of the profile until the operator confirms it.
- Link pages with ordinary markdown links, relative to the page. Links under `## See also` need no source.
- When a newer source contradicts an older fact, keep the old line, strike it through with `~~` and add the newer fact after it, so the history stays visible.
- One topic per page. Prefer adding to a page over starting a new one.

## Pages the operator edited

If the operator edited a page by hand, never rewrite it whole. Add to it, or change one part with edit.
