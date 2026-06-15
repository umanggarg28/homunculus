"""Spaced-repetition store for the quiz coach.

The deterministic half of the coach: WHICH concept is due for review and
HOW the interval changes after an answer. A Leitner-box schedule —
correct answers promote a topic to a longer interval, wrong answers
reset it to daily. The LLM composes the question and grades the
free-text answer; it must never decide scheduling (a model eyeballing
"what's due" reviews the same easy topic forever and never resurfaces
the hard ones).

Mirrors TaskStore: a single JSON file, atomic writes, pure-data methods
that are trivially unit-testable. State (workspace/quiz.json) survives
restarts and rebuilds.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# Leitner intervals in days, indexed by box. Box 0 = brand new / just
# missed → ask tomorrow; each promotion roughly doubles the gap up to a
# month. A topic that's been answered right five times is "known" and
# resurfaces only monthly.
_INTERVALS_DAYS = [1, 2, 4, 9, 18, 30]
_MAX_BOX = len(_INTERVALS_DAYS) - 1

# Outcomes the coach may report. "partial" keeps the box but re-asks
# soon; it neither rewards nor punishes a half-right answer.
_OUTCOMES = {"correct", "partial", "wrong"}


def _now() -> datetime:
    # Naive user-local wall clock, consistent with TaskStore timestamps.
    from user_tz import now_user_naive
    return now_user_naive()


class QuizStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    # --- persistence -----------------------------------------------------

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"topics": [], "pending": None}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"topics": [], "pending": None}
        data.setdefault("topics", [])
        data.setdefault("pending", None)
        return data

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    # --- topic management ------------------------------------------------

    # --- learning area (the one human-set input) -------------------------

    def set_area(self, area: str) -> str:
        """Set the broad domain the coach quizzes within (e.g. 'deep
        learning'). This is the ONLY human-supplied input — the agent
        autonomously chooses sub-topics inside it and composes questions."""
        data = self._load()
        data["area"] = (area or "").strip()
        self._save(data)
        return data["area"]

    def get_area(self) -> str:
        return self._load().get("area", "")

    def add_topics(self, topics: list[str]) -> int:
        """Add new topics (idempotent by title). Returns count added."""
        data = self._load()
        existing = {t["topic"] for t in data["topics"]}
        added = 0
        for title in topics:
            title = title.strip()
            if not title or title in existing:
                continue
            data["topics"].append({
                "topic": title,
                "box": 0,
                "due_at": _now().isoformat(timespec="seconds"),
                "last_reviewed": None,
                "seen": 0,
                "correct": 0,
            })
            existing.add(title)
            added += 1
        if added:
            self._save(data)
        return added

    def _due_topics(self, topics: list[dict], now: datetime) -> list[dict]:
        return [t for t in topics if t.get("due_at", "") <= now.isoformat()]

    def _set_pending(self, data: dict, topic_title: str, now: datetime) -> None:
        data["pending"] = {"topic": topic_title, "asked_at": now.isoformat(timespec="seconds")}

    def pick(self, topic: str = "") -> dict:
        """Decide what to quiz, OR commit an agent-chosen topic.

        Modes returned in the result dict:
          - "review":  a due topic to re-ask (pending set) — harness picked it.
          - "explore": nothing due → the agent should pick a NEW sub-topic
                       within `area` (avoiding `covered`) and call
                       pick(topic=<that topic>). No pending set yet.
          - "ask":     the agent supplied `topic` — registered (if new) and
                       marked pending, ready to ask.

        Stale-pending rotation: a question asked on an EARLIER day that was
        never graded is lapsed (rescheduled, no score change) instead of
        being re-asked forever — the old behavior repeated the same
        question every day until the user happened to grade it.
        """
        data = self._load()
        now = _now()
        topic = (topic or "").strip()

        # Agent committed a (newly explored or re-asked) topic → register + pend.
        if topic:
            existing = next((t for t in data["topics"] if t["topic"] == topic), None)
            if existing is None:
                existing = {
                    "topic": topic, "box": 0,
                    "due_at": now.isoformat(timespec="seconds"),
                    "last_reviewed": None, "seen": 0, "correct": 0,
                }
                data["topics"].append(existing)
            self._set_pending(data, topic, now)
            self._save(data)
            return {**existing, "mode": "ask", "already_pending": False}

        # Lapse a stale pending (asked on a prior day, never graded).
        pending = data.get("pending")
        if pending:
            asked = pending.get("asked_at", "")
            if asked and asked[:10] >= now.date().isoformat():
                # Asked today, not yet graded — re-ask the SAME one (don't
                # stack two questions in a day).
                cur = next((t for t in data["topics"] if t["topic"] == pending["topic"]), None)
                if cur is not None:
                    return {**cur, "mode": "review", "already_pending": True}
            lapsed = next((t for t in data["topics"] if t["topic"] == pending["topic"]), None)
            if lapsed is not None:
                iv = _INTERVALS_DAYS[min(int(lapsed.get("box", 0)), _MAX_BOX)]
                lapsed["due_at"] = (now + timedelta(days=iv)).isoformat(timespec="seconds")
            data["pending"] = None

        # A due topic to review? Most-overdue first; tie-break on oldest
        # last_reviewed so equal-due topics rotate instead of repeating.
        due = self._due_topics(data["topics"], now)
        if due:
            chosen = min(due, key=lambda t: (t.get("due_at", ""), t.get("last_reviewed") or ""))
            self._set_pending(data, chosen["topic"], now)
            self._save(data)
            overdue_days = (now - datetime.fromisoformat(chosen["due_at"])).days
            return {**chosen, "mode": "review", "already_pending": False,
                    "overdue_days": max(0, overdue_days)}

        # Nothing due → tell the agent to EXPLORE a new sub-topic in the area.
        self._save(data)
        covered = [
            t["topic"] for t in sorted(
                data["topics"],
                key=lambda t: t.get("last_reviewed") or t.get("due_at", ""),
                reverse=True,
            )[:20]
        ]
        return {"mode": "explore", "area": data.get("area", ""), "covered": covered}

    def grade(self, outcome: str) -> dict:
        """Apply an outcome to the pending topic and reschedule it.

        correct → promote a box (longer interval).
        wrong   → reset to box 0 (ask again tomorrow).
        partial → keep the box but re-ask in a day.
        """
        outcome = (outcome or "").strip().lower()
        if outcome not in _OUTCOMES:
            return {"error": f"outcome must be one of {sorted(_OUTCOMES)}"}
        data = self._load()
        pending = data.get("pending")
        if not pending:
            return {"error": "no pending question — call quiz_pick first"}
        topic = next((t for t in data["topics"] if t["topic"] == pending["topic"]), None)
        if topic is None:
            data["pending"] = None
            self._save(data)
            return {"error": "pending topic no longer exists; cleared"}

        now = _now()
        topic["seen"] = int(topic.get("seen", 0)) + 1
        if outcome == "correct":
            topic["box"] = min(_MAX_BOX, int(topic.get("box", 0)) + 1)
            topic["correct"] = int(topic.get("correct", 0)) + 1
            interval = _INTERVALS_DAYS[topic["box"]]
        elif outcome == "wrong":
            topic["box"] = 0
            interval = _INTERVALS_DAYS[0]
        else:  # partial
            interval = 1
        topic["due_at"] = (now + timedelta(days=interval)).isoformat(timespec="seconds")
        topic["last_reviewed"] = now.isoformat(timespec="seconds")
        data["pending"] = None
        self._save(data)
        return {
            "topic": topic["topic"],
            "outcome": outcome,
            "box": topic["box"],
            "next_review_in_days": interval,
            "next_due": topic["due_at"],
            "seen": topic["seen"],
            "accuracy": round(topic["correct"] / topic["seen"], 2) if topic["seen"] else 0.0,
        }

    def stats(self) -> dict:
        """Snapshot for reports: counts, due-now, and weakest topics."""
        data = self._load()
        topics = data["topics"]
        now = _now()
        due_now = [t for t in topics if t.get("due_at", "") <= now.isoformat()]
        weakest = sorted(
            topics,
            key=lambda t: (int(t.get("box", 0)), -int(t.get("seen", 0))),
        )[:5]
        return {
            "total_topics": len(topics),
            "due_now": len(due_now),
            "pending": data.get("pending"),
            "weakest": [{"topic": t["topic"], "box": t.get("box", 0), "seen": t.get("seen", 0)} for t in weakest],
        }


def _store() -> QuizStore:
    path = Path(os.environ.get("HOMUNCULUS_QUIZ_FILE", "./quiz.json"))
    return QuizStore(path)
