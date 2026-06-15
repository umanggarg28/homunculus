"""Quiz-coach tool wrappers over the spaced-repetition store (quiz.py).

The harness owns scheduling (what's due, the spaced-repetition intervals,
rotation); the agent owns judgment (which NEW sub-topic to explore within
the area, composing the question, grading the answer). The agent chooses
and researches sub-topics autonomously — there is no human-supplied topic
list.
"""

from __future__ import annotations

import json

from quiz import _store


def quiz_pick(topic: str = "") -> str:
    """Decide what to quiz — or commit a topic you chose.

    Call with NO argument first. You'll get one of:
      • mode "review"  — a due topic to re-ask (already selected for you).
      • mode "explore" — nothing is due. Pick a NEW sub-topic within the
        returned `area` that is NOT in `recently_covered`, research it with
        web_search to ground a fresh, correct question, then call
        quiz_pick(topic="<the sub-topic you chose>") to commit it.
      • mode "ask"     — (returned after you pass a topic) the topic is
        registered and pending; compose and send the question now.
    """
    result = _store().pick(topic=topic)
    mode = result.get("mode")

    if mode == "explore":
        area = result.get("area") or ""
        return json.dumps({
            "status": "explore",
            "area": area,
            "recently_covered": result.get("covered", []),
            "note": (
                f"Nothing is due for review. Choose ONE specific sub-topic within "
                f"'{area}' that is NOT in recently_covered, web_search it to ground "
                "an accurate question, then call quiz_pick(topic=\"<your sub-topic>\") "
                "to register it before asking."
                if area else
                "No learning area is set yet — ask the user which broad domain they "
                "want to be quizzed on before proceeding."
            ),
        }, indent=2)

    if result.get("already_pending"):
        return json.dumps({
            "status": "pending",
            "topic": result["topic"],
            "note": "This question is still awaiting an answer from earlier today — "
                    "re-ask the SAME topic, don't pick a new one.",
        }, indent=2)

    # mode "review" or "ask" — a concrete topic, pending set.
    return json.dumps({
        "status": "picked",
        "topic": result["topic"],
        "mode": mode,
        "box": result.get("box", 0),
        "seen": result.get("seen", 0),
        "overdue_days": result.get("overdue_days", 0),
        "note": "Compose ONE question on this topic (web_search first if it helps "
                "accuracy/recency), send it via notify, and grade the reply next "
                "turn with quiz_grade.",
    }, indent=2)


def quiz_grade(outcome: str) -> str:
    """Record the outcome of the pending question. outcome ∈
    {correct, partial, wrong}."""
    return json.dumps(_store().grade(outcome), indent=2)
