#!/usr/bin/env python3
"""Bootstrap the spaced-repetition quiz coach.

Run once after deploying the quiz tools:
  $ docker compose exec heartbeat uv run python /app/scripts/bootstrap_quiz_coach.py

Idempotent:
  - seeds the topic bank (add_topics skips ones already present)
  - writes skill_quiz_coach.md only if absent
  - creates the evening "quiz-coach" task only if absent

Schedules for today/tomorrow at 20:00 in the user's timezone — after the
work day, well clear of the 07:00 brief and the Sunday digest.
"""

from __future__ import annotations

import os
import sys
from datetime import timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.environ.setdefault("HOMUNCULUS_USER_TZ_FILE", str(REPO / "workspace" / "user_tz.txt"))

# Deep-learning + ML-interview concepts, weighted to what Umang is
# building from scratch (nanoGPT, micrograd, transformers, RL). The bank
# grows over time — the agent can add topics via the store too.
SEED_TOPICS = [
    "Backpropagation: deriving the chain rule through a computation graph",
    "Why we need non-linear activation functions",
    "Vanishing and exploding gradients: causes and fixes",
    "Batch normalization: what it normalizes and why it helps",
    "Layer normalization vs batch normalization",
    "Dropout: train-time vs test-time behavior",
    "Self-attention: the query/key/value formulation",
    "Multi-head attention: why multiple heads",
    "Positional encodings in transformers",
    "Why scaled dot-product attention divides by sqrt(d_k)",
    "Cross-entropy loss and its gradient w.r.t. logits",
    "Softmax: numerical stability via the max-subtraction trick",
    "Adam optimizer: what the first and second moments track",
    "Learning rate warmup and decay schedules",
    "Weight initialization: Xavier vs He",
    "The bias-variance tradeoff",
    "Overfitting: how to detect and regularize against it",
    "L1 vs L2 regularization: sparsity vs weight shrinkage",
    "Gradient descent variants: batch, mini-batch, stochastic",
    "Residual connections: why they ease deep-network training",
    "Tokenization: BPE and why subword units",
    "KV-cache in autoregressive decoding",
    "Temperature and top-k/top-p sampling",
    "The exploration-exploitation tradeoff in RL",
    "Policy gradients: the REINFORCE estimator",
    "Q-learning vs policy-based methods",
    "Embeddings: what a learned vector space captures",
    "Convolution: parameter sharing and translation equivariance",
    "Why transformers replaced RNNs for long sequences",
    "Teacher forcing and exposure bias",
]

SKILL_BODY = """---
name: skill_quiz_coach
description: Run the daily spaced-repetition quiz — ask one concept in the evening, grade the user's reply, let the harness schedule what's due next.
type: skill
states:
  - tool: quiz_pick
  - tool: notify
  - tool: complete_task
related: [[skill_daily_brief]]
---

# Quiz Coach — execution playbook

## What this is
A spaced-repetition coach. The HARNESS decides which concept is due
(quiz_pick) and how the interval moves after grading (quiz_grade) —
you only write the question and judge the answer. Never pick topics
yourself; never decide scheduling.

## Phase 1 — Evening tick (the recurring task fires)

1. **Call `quiz_pick()`.** It returns the concept to quiz and marks it
   pending. If `status` is `no_topics`, send a one-line note saying the
   bank is empty and complete the task. If `already_pending`, the last
   question was never answered — re-ask that SAME topic.
2. **Compose ONE question** on the returned `topic`. Make it a real
   question that probes understanding, not a definition prompt. Vary
   the angle on repeat topics (derive it, compare it, apply it).
3. **Call `notify(text=...)`** with the question. Open with `🧠 Quiz —`
   so it's recognizable. Tell the user to just reply with their answer.
4. **Call `complete_task(task_id="quiz-coach", result="Asked: <topic>")`.**

Do NOT grade in this phase — the user hasn't answered yet.

## Phase 2 — Grading (when the user replies in chat)

When the user sends a message that is clearly answering the quiz
question (you'll see your `🧠 Quiz` question earlier in the
conversation, and quiz_pick state is pending):

1. Judge their answer against the concept: `correct` (got the key
   idea), `partial` (right direction, missed something important), or
   `wrong` (incorrect or "I don't know").
2. **Call `quiz_grade(outcome=...)`** with that judgment.
3. Reply with brief, warm feedback: confirm what they got right, fill
   the gap if any, in 2–4 sentences. Mention when it'll come back up
   (quiz_grade returns `next_review_in_days`).

Be encouraging. The point is durable learning, not a test score. A
"wrong" is a topic to revisit, said kindly.

## Success criteria (mirror the task)
- `notify_called` — exactly one notify() in the evening tick
- `notify_min_chars` ≥ 40
- `notify_contains` "Quiz"

## Watch outs
- One question per evening. quiz_pick gives one; don't batch.
- If the user ignores a question for days, quiz_pick keeps returning it
  as pending — just re-ask it; nothing is lost.
- Grading is a chat interaction, not a scheduled task. If the user
  answers hours later, grade it then.
"""


def _next_8pm_user_naive() -> str:
    """Next 20:00 as a NAIVE wall-clock string in the user's timezone —
    the frame TaskStore stores due_at in (now_user_naive). Avoids
    constructing ZoneInfo from a possibly-abbreviated name like macOS's
    'IST' (not a valid IANA key); now_user_naive only trusts a stored
    IANA name and otherwise uses system-local wall clock."""
    from user_tz import now_user_naive
    now_local = now_user_naive()
    target = now_local.replace(hour=20, minute=0, second=0, microsecond=0)
    if target <= now_local:
        target += timedelta(days=1)
    return target.isoformat(timespec="seconds")


def _seed_topics() -> None:
    from quiz import QuizStore
    path = Path(os.environ.get("HOMUNCULUS_QUIZ_FILE", str(REPO / "workspace" / "quiz.json")))
    added = QuizStore(path).add_topics(SEED_TOPICS)
    print(f"[bootstrap_quiz_coach] seeded {added} new topics into {path}")


def _ensure_skill_file() -> None:
    memory_dir = Path(os.environ.get("HOMUNCULUS_MEMORY_DIR", str(REPO / "workspace" / "memory")))
    memory_dir.mkdir(parents=True, exist_ok=True)
    target = memory_dir / "skill_quiz_coach.md"
    if target.exists():
        print(f"[bootstrap_quiz_coach] skill file exists — leaving alone: {target}")
        return
    target.write_text(SKILL_BODY, encoding="utf-8")
    print(f"[bootstrap_quiz_coach] wrote skill file: {target}")


def main() -> int:
    from tasks import TaskStore

    _seed_topics()
    _ensure_skill_file()

    store = TaskStore(Path(os.environ.get("HOMUNCULUS_TASKS_DIR", str(REPO / "workspace" / "tasks"))))
    existing = next((t for t in store.all() if t["id"] == "quiz-coach"), None)
    if existing is not None:
        print("[bootstrap_quiz_coach] task already exists — leaving alone.")
        print(f"  due_at: {existing.get('due_at')}  status: {existing.get('status')}")
        return 0

    due_at = _next_8pm_user_naive()
    task = store.create(
        title="Quiz coach",
        description=(
            "Run the daily spaced-repetition quiz — see skill_quiz_coach.md. "
            "Call quiz_pick, ask ONE question on the returned concept via "
            "notify, then grade the user's reply with quiz_grade."
        ),
        due_at=due_at,
        recurrence="daily",
        notify=True,
        success_criteria=[
            {"type": "notify_called"},
            {"type": "notify_min_chars", "n": 40},
            {"type": "notify_contains", "text": "Quiz"},
        ],
        skill="skill_quiz_coach",
    )
    print(f"[bootstrap_quiz_coach] created task '{task['id']}'")
    print(f"  due_at:     {task['due_at']} (next 20:00 user-local)")
    print(f"  recurrence: {task['recurrence']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
