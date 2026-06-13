"""Quiz-coach tool wrappers over the spaced-repetition store (quiz.py).

Thin pass-throughs that return JSON for the LLM to act on: pick the
due concept, then grade the user's free-text answer. Scheduling lives
in quiz.QuizStore; the model only writes the question and judges the
answer.
"""

from __future__ import annotations

import json

from quiz import _store


def quiz_pick() -> str:
    topic = _store().pick()
    if topic is None:
        return json.dumps({
            "status": "no_topics",
            "message": "No quiz topics seeded yet. Add some with the bootstrap script.",
        })
    return json.dumps({
        "status": "pending" if topic.get("already_pending") else "picked",
        "topic": topic["topic"],
        "box": topic.get("box", 0),
        "seen": topic.get("seen", 0),
        "overdue_days": topic.get("overdue_days", 0),
        "note": (
            "This question is already awaiting an answer from a previous "
            "run — re-ask the SAME topic, don't pick a new one."
            if topic.get("already_pending")
            else "Compose ONE question on this topic, send it via notify, "
            "and wait for the user's answer. Grade it next turn with quiz_grade."
        ),
    }, indent=2)


def quiz_grade(outcome: str) -> str:
    """Record the outcome of the pending question. outcome ∈
    {correct, partial, wrong}."""
    return json.dumps(_store().grade(outcome), indent=2)
