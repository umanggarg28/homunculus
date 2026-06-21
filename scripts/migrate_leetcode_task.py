"""One-off migration: move the daily-LeetCode task onto the harness-owned
delivery machinery (PRs #142-#145).

- Rewrites skill_deliver_daily_leetcode.md via Skills.save() (versioned,
  prior body archived to .skill_history/) with a `states:` declaration
  and a playbook that embeds the canonical Top Interview 150 order.
- Adds notify_matches (leetcode.com link required) + notify_unique
  (slug dedupe against the ledger) to the task's success_criteria.
- Seeds the task's `delivered` ledger from the old LLM-maintained
  tracker file, then marks that file deprecated.

Run from the repo root:  uv run python scripts/migrate_leetcode_task.py
Idempotent — safe to re-run.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.environ.setdefault("HOMUNCULUS_USER_TZ_FILE", str(REPO / "workspace" / "user_tz.txt"))

from homunculus.skills import Skills  # noqa: E402
from homunculus.tasks import TaskStore  # noqa: E402

MEMORY_ROOT = REPO / "workspace" / "memory"
TASKS_ROOT = REPO / "workspace" / "tasks"
TASK_ID = "daily-leetcode-150-problem-at-9-am-ist"
SLUGS_FILE = Path("/tmp/top150_slugs.txt")

# Problems the old markdown tracker says were already sent.
ALREADY_DELIVERED = [
    "two-sum",
    "merge-sorted-array",
    "remove-element",
    "remove-duplicates-from-sorted-array",
    "best-time-to-buy-and-sell-stock",
    "contains-duplicate",
    "valid-parentheses",
    "remove-duplicates-from-sorted-array-ii",
    "majority-element",
    "rotate-array",
    "best-time-to-buy-and-sell-stock-ii",
]

SKILL_TEMPLATE = """---
name: skill_deliver_daily_leetcode
description: Deliver the next undelivered LeetCode Top Interview 150 problem with solution via Telegram
type: skill
states:
  - tool: web_post
  - tool: notify
  - tool: complete_task
---

# Daily LeetCode delivery — playbook

Goal: ONE Telegram message with the NEXT undelivered problem from the
ordered list below, plus a working Python solution you write yourself.
The harness forces the tool order: web_post → notify → complete_task.

## Steps

1. Pick the slug: the FIRST item under "Problem order" that is NOT in
   the task's `already_delivered` list. The task block carries the
   ledger — no file reads needed.
2. web_post — fetch the problem statement from LeetCode GraphQL.
   web_search and HTML scraping are forbidden (they 403 or surface the
   wrong site):
   - url: `https://leetcode.com/graphql/`
   - json_body: `{"query": "query q($titleSlug: String!) { question(titleSlug: $titleSlug) { title difficulty content topicTags { name } } }", "variables": {"titleSlug": "<slug>"}}`
   - headers: `{"Referer": "https://leetcode.com/problems/<slug>/"}`
3. notify — one message containing, in order:
   - Title + difficulty
   - The canonical link `https://leetcode.com/problems/<slug>/` —
     REQUIRED; the success criteria reject the message without it
   - Problem summary in 2-4 plain-text sentences (from `content`)
   - Approach in 2-3 sentences
   - A working solution in a fenced ```python block, with a one-line
     time/space complexity note
4. complete_task(task_id, result="Delivered <slug>")

## Failure handling

- If web_post errors, retry ONCE with the same body. If it fails again,
  call record_failure(task_id, reason). Do NOT improvise from
  web_search, and do NOT call complete_task without a delivered
  problem — the harness refuses it.

## Problem order (Top Interview 150, canonical)

{slug_list}
"""


def main() -> None:
    slugs = SLUGS_FILE.read_text(encoding="utf-8").split()
    assert len(slugs) == 150, f"expected 150 slugs, got {len(slugs)}"

    # Wrap the slug list ~5 per line to keep the file diffable.
    lines = [", ".join(slugs[i:i + 5]) for i in range(0, len(slugs), 5)]
    body = SKILL_TEMPLATE.replace("{slug_list}", "\n".join(lines))

    version = Skills(MEMORY_ROOT).save(
        "skill_deliver_daily_leetcode",
        body,
        source="manual",
        rationale=(
            "migrate to harness-owned delivery: states declaration, embedded "
            "canonical Top-150 order, ledger-based dedupe (PRs #142-#145)"
        ),
    )
    print(f"skill saved as v{version}")

    store = TaskStore(TASKS_ROOT)
    task = store.get(TASK_ID)
    assert task is not None, f"task {TASK_ID!r} not found"

    criteria = [
        {"type": "notify_called"},
        {"type": "notify_min_chars", "n": 200},
        {"type": "notify_has_code"},
        {"type": "notify_matches", "pattern": r"leetcode\.com/problems/"},
        {"type": "notify_unique", "pattern": r"leetcode\.com/problems/([a-z0-9-]+)"},
    ]
    store.update(TASK_ID, success_criteria=criteria)
    print("success_criteria updated")

    for slug in ALREADY_DELIVERED:
        store.record_delivery(TASK_ID, slug)
    ledger = [d["key"] for d in (store.get(TASK_ID).get("delivered") or [])]
    print(f"ledger seeded: {len(ledger)} keys: {', '.join(ledger)}")

    tracker = MEMORY_ROOT / "project_delivered_leetcode_problems.md"
    if tracker.exists() and "DEPRECATED" not in tracker.read_text(encoding="utf-8"):
        tracker.write_text(
            "---\n"
            "name: project_delivered_leetcode_problems\n"
            "description: DEPRECATED — delivered problems now live in the task's harness-owned ledger\n"
            "type: project\n"
            "---\n\n"
            "DEPRECATED 2026-06-11. The delivered-problem list moved to the\n"
            "`delivered` ledger on task `daily-leetcode-150-problem-at-9-am-ist`\n"
            "(workspace/tasks/tasks.json), maintained by the heartbeat harness.\n"
            "Do not update this file.\n",
            encoding="utf-8",
        )
        print("old tracker marked deprecated")


if __name__ == "__main__":
    main()
