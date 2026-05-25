"""End-to-end test of the notify → Telegram-history bridge.

Simulates the bug we just fixed:
  1. Heartbeat (or any process) sends a leetcode problem via notify()
  2. User replies "explain it" in Telegram
  3. The Telegram bot drains pending notifications into agent.history
  4. The agent's reply must reference the leetcode problem (not confabulate)

Run inside the homunculus container so env/credentials match prod:
    docker compose exec telegram uv run --project /app python -m scripts.test_notify_bridge

Or from the host (requires .env on host):
    uv run python -m scripts.test_notify_bridge
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import tools  # noqa: E402  (must come after load_dotenv)
from core import Agent, SYSTEM_PROMPT  # noqa: E402
from memory import Memory  # noqa: E402
from transports.telegram import (  # noqa: E402
    TELEGRAM_PROMPT_SUFFIX,
    _drain_notifications_into_history,
)


LEETCODE_PROBLEM = """Daily LeetCode · Problem 198: House Robber (Medium)

You are a professional robber planning to rob houses along a street. Each
house has a certain amount of money stashed. The only constraint stopping
you from robbing each of them is that adjacent houses have security
systems connected — robbing two adjacent houses on the same night will
alert the police.

Given an integer array `nums` representing the amount of money at each
house, return the maximum amount of money you can rob tonight without
alerting the police.

Example:
  Input:  nums = [2, 7, 9, 3, 1]
  Output: 12   (rob house 0, 2, 4 → 2 + 9 + 1 = 12)

Solution sketch:
  dp[i] = max(dp[i-1], dp[i-2] + nums[i])
  Time: O(n), space: O(1) with two rolling vars.
"""

USER_FOLLOWUP = "explain it"


def section(title: str) -> None:
    print(f"\n{'═' * 64}")
    print(f"  {title}")
    print("═" * 64)


def main() -> int:
    section("SETUP")
    memory_dir = Path(os.environ.get("HOMUNCULUS_MEMORY_DIR", "./memory"))
    print(f"  memory dir: {memory_dir.resolve()}")
    memory = Memory(memory_dir)

    # Wipe any prior notification state so this run is deterministic.
    if memory.notifications_path.exists():
        memory.notifications_path.unlink()
        print("  cleared existing notifications.jsonl")
    if memory._notifications_pointer_path.exists():  # noqa: SLF001
        memory._notifications_pointer_path.unlink()  # noqa: SLF001
        print("  cleared existing consumption pointer")

    section("STEP 1 — heartbeat queues the leetcode notification")
    # Skip the actual Telegram HTTP send (would spam the user). Just exercise
    # the queue helper directly — same code path notify() calls internally
    # after a successful send.
    memory.queue_notification(LEETCODE_PROBLEM)
    raw = memory.notifications_path.read_text(encoding="utf-8").strip()
    queued = [json.loads(line) for line in raw.splitlines() if line.strip()]
    assert len(queued) == 1, f"expected 1 queued entry, got {len(queued)}"
    print(f"  queued 1 entry · ts={queued[0]['ts']:.0f} · "
          f"text={queued[0]['text'][:60]}…")

    section("STEP 2 — Telegram bot wakes, drains queue into agent history")
    tools.init(memory, autonomous=True)
    agent = Agent(memory=memory, system_prompt=SYSTEM_PROMPT + TELEGRAM_PROMPT_SUFFIX)
    # NB: do NOT call restore_session here — we want a fresh history so the
    # test isolates the bridge behavior from prior conversations.
    print(f"  starting history len: {len(agent.history)} (system prompt only)")

    _drain_notifications_into_history(agent)
    print(f"  post-drain history len: {len(agent.history)}")
    last = agent.history[-1]
    assert last["role"] == "assistant", "drain should add an assistant message"
    assert "House Robber" in last["content"], "leetcode text must be in history"
    print(f"  injected assistant message preview: "
          f"{last['content'][:100]}…")

    section("STEP 3 — second drain is a no-op (pointer advanced)")
    before = len(agent.history)
    _drain_notifications_into_history(agent)
    after = len(agent.history)
    assert before == after, "second drain must not re-inject"
    print(f"  history unchanged · {before} → {after}")

    section("STEP 4 — user sends 'explain it' → real LLM call")
    print(f"  user: {USER_FOLLOWUP!r}")
    t0 = time.time()
    reply = agent.chat(USER_FOLLOWUP)
    dt = time.time() - t0
    print(f"\n  --- agent reply ({dt:.1f}s) ---")
    print("  " + reply.replace("\n", "\n  "))
    print(f"  --- end reply ---")

    section("STEP 5 — grading")
    reply_lower = reply.lower()
    # Buckets of synonyms — each bucket counts as one hit if ANY needle
    # in it appears. The model can phrase the same idea many ways
    # ("adjacent" vs "neighboring", "dp" vs "best[i]", etc.) and we
    # don't want to penalize correct answers that pick equivalents.
    buckets: list[tuple[str, list[str]]] = [
        ("names the problem / domain",
            ["house robber", "houses", "rob "]),
        ("adjacency constraint",
            ["adjacent", "neighbor", "consecutive", "i-1", "i‑1"]),
        ("DP recurrence form",
            ["dp", "best[i", "max(", "recurrence", "prev1", "prev2"]),
        ("input variable",
            ["nums", "array", "values"]),
        ("complexity / space optimization",
            ["o(n)", "o(1)", "space", "rolling", "two variables", "two extra"]),
    ]
    hits = 0
    print("  Evidence the agent had the notification context:")
    for label, needles in buckets:
        matched = next((n for n in needles if n in reply_lower), None)
        if matched:
            hits += 1
            print(f"    ✓ {label:32}  — matched on {matched!r}")
        else:
            print(f"    ✗ {label:32}  — none of {needles!r}")

    verdict = "PASS" if hits >= 3 else "FAIL"
    print(f"\n  VERDICT: {verdict}  "
          f"({hits}/{len(buckets)} buckets matched; need ≥3 to confirm)")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
