"""QuizStore — the deterministic spaced-repetition scheduler.

The coach's value is that the HARNESS decides what's due and how the
interval moves, not the LLM (which would re-quiz the easy topic forever
and bury the hard ones). These tests pin the Leitner mechanics: correct
promotes and lengthens the interval, wrong resets to daily, the
most-overdue topic surfaces first, and one question is pending at a time.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from quiz import QuizStore, _INTERVALS_DAYS, _MAX_BOX
from tests.conftest import load_real_tool_submodule


@pytest.fixture()
def store(tmp_path):
    return QuizStore(tmp_path / "quiz.json")


def _set_due(store, topic_title, when: datetime):
    data = store._load()
    for t in data["topics"]:
        if t["topic"] == topic_title:
            t["due_at"] = when.isoformat(timespec="seconds")
    store._save(data)


# ---- topic management ---------------------------------------------------


def test_add_topics_is_idempotent(store):
    assert store.add_topics(["Backprop", "Attention"]) == 2
    assert store.add_topics(["Backprop", "Dropout"]) == 1  # only Dropout is new
    assert store.stats()["total_topics"] == 3


def test_pick_returns_none_when_empty(store):
    assert store.pick() is None


# ---- selection ----------------------------------------------------------


def test_most_overdue_topic_is_picked_first(store):
    store.add_topics(["A", "B", "C"])
    now = datetime.now()
    _set_due(store, "A", now - timedelta(days=1))
    _set_due(store, "B", now - timedelta(days=10))  # most overdue
    _set_due(store, "C", now + timedelta(days=5))
    assert store.pick()["topic"] == "B"


def test_pick_marks_pending_and_does_not_stack(store):
    store.add_topics(["A", "B"])
    first = store.pick()
    assert first["already_pending"] is False
    # A second pick before grading returns the SAME pending topic.
    second = store.pick()
    assert second["already_pending"] is True
    assert second["topic"] == first["topic"]


# ---- grading / scheduling ----------------------------------------------


def test_correct_promotes_box_and_lengthens_interval(store):
    store.add_topics(["Backprop"])
    store.pick()
    res = store.grade("correct")
    assert res["box"] == 1
    assert res["next_review_in_days"] == _INTERVALS_DAYS[1]
    assert res["accuracy"] == 1.0


def test_wrong_resets_to_box_zero(store):
    store.add_topics(["Backprop"])
    # Promote twice, then miss.
    for _ in range(2):
        store.pick()
        store.grade("correct")
    store.pick()
    res = store.grade("wrong")
    assert res["box"] == 0
    assert res["next_review_in_days"] == _INTERVALS_DAYS[0]


def test_partial_keeps_box_but_reasks_soon(store):
    store.add_topics(["Backprop"])
    store.pick()
    store.grade("correct")  # box 1
    store.pick()
    res = store.grade("partial")
    assert res["box"] == 1  # unchanged
    assert res["next_review_in_days"] == 1


def test_box_caps_at_max(store):
    store.add_topics(["Backprop"])
    for _ in range(_MAX_BOX + 3):
        store.pick()
        store.grade("correct")
    data = store._load()
    assert data["topics"][0]["box"] == _MAX_BOX


def test_grade_without_pending_is_an_error(store):
    store.add_topics(["A"])
    assert "error" in store.grade("correct")


def test_invalid_outcome_rejected(store):
    store.add_topics(["A"])
    store.pick()
    assert "error" in store.grade("excellent")


def test_grading_clears_pending_so_next_pick_advances(store):
    store.add_topics(["A", "B"])
    now = datetime.now()
    _set_due(store, "A", now - timedelta(days=5))
    _set_due(store, "B", now - timedelta(days=1))
    first = store.pick()["topic"]  # A (more overdue)
    store.grade("correct")          # A pushed out
    second = store.pick()["topic"]  # now B is most due
    assert first == "A" and second == "B"


# ---- stats --------------------------------------------------------------


def test_stats_reports_weakest_first(store):
    store.add_topics(["Easy", "Hard"])
    # Make "Easy" strong.
    for _ in range(3):
        _set_due(store, "Easy", datetime.now() - timedelta(days=30))
        store.pick()
        store.grade("correct")
    s = store.stats()
    assert s["total_topics"] == 2
    assert s["weakest"][0]["topic"] == "Hard"  # box 0, lowest


def test_corrupt_file_recovers_as_empty(store):
    store.path.write_text("{bad json", encoding="utf-8")
    assert store.pick() is None
    assert store.add_topics(["A"]) == 1


# ---- tool wrappers ------------------------------------------------------


def test_tool_wrappers_return_json(tmp_path, monkeypatch):
    import json
    monkeypatch.setenv("HOMUNCULUS_QUIZ_FILE", str(tmp_path / "quiz.json"))
    coach = load_real_tool_submodule("coach")

    # No topics yet.
    assert json.loads(coach.quiz_pick())["status"] == "no_topics"

    QuizStore(tmp_path / "quiz.json").add_topics(["Backprop"])
    picked = json.loads(coach.quiz_pick())
    assert picked["status"] == "picked"
    assert picked["topic"] == "Backprop"

    graded = json.loads(coach.quiz_grade("correct"))
    assert graded["box"] == 1
