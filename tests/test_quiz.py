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

from homunculus.quiz import QuizStore, _INTERVALS_DAYS, _MAX_BOX
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


def test_pick_explores_when_empty(store):
    store.set_area("deep learning")
    res = store.pick()
    assert res["mode"] == "explore"
    assert res["area"] == "deep learning"


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


def test_pick_arms_pending_undelivered(store):
    """A freshly picked pending is NOT yet delivered — the badge stays off
    until the harness confirms the question actually went out."""
    store.add_topics(["A"])
    store.pick()
    pending = store._load()["pending"]
    assert pending["delivered"] is False


def test_confirm_delivered_then_clear(store):
    store.add_topics(["A"])
    store.pick()
    assert store.confirm_delivered() is True
    assert store._load()["pending"]["delivered"] is True
    assert store.clear_pending() is True
    assert store._load()["pending"] is None
    # Idempotent / safe when there's nothing pending.
    assert store.confirm_delivered() is False
    assert store.clear_pending() is False


# ---- grading / scheduling ----------------------------------------------


def test_correct_promotes_box_and_lengthens_interval(store):
    store.add_topics(["Backprop"])
    store.pick()
    res = store.grade("correct")
    assert res["box"] == 1
    assert res["next_review_in_days"] == _INTERVALS_DAYS[1]
    assert res["accuracy"] == 1.0


def test_wrong_resets_to_box_zero(store):
    # pick(topic=...) commits the same topic regardless of due-timing, so
    # box mechanics can be tested without forcing due_at each round.
    for _ in range(2):
        store.pick(topic="Backprop")
        store.grade("correct")
    store.pick(topic="Backprop")
    res = store.grade("wrong")
    assert res["box"] == 0
    assert res["next_review_in_days"] == _INTERVALS_DAYS[0]


def test_partial_keeps_box_but_reasks_soon(store):
    store.pick(topic="Backprop")
    store.grade("correct")  # box 1
    store.pick(topic="Backprop")
    res = store.grade("partial")
    assert res["box"] == 1  # unchanged
    assert res["next_review_in_days"] == 1


def test_box_caps_at_max(store):
    for _ in range(_MAX_BOX + 3):
        store.pick(topic="Backprop")
        store.grade("correct")
    data = store._load()
    assert data["topics"][0]["box"] == _MAX_BOX


def test_grade_without_pending_is_an_error(store):
    store.add_topics(["A"])
    assert "error" in store.grade("correct")


def test_invalid_outcome_rejected(store):
    store.pick(topic="A")
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
    assert store.pick()["mode"] == "explore"
    assert store.add_topics(["A"]) == 1


# ---- autonomous: stale-pending rotation + explore + commit --------------


def test_stale_pending_lapses_instead_of_repeating(store):
    """The reported bug: a question asked on a prior day and never graded
    was re-asked every day forever. It must lapse (reschedule, no score
    change) and let a different topic surface."""
    store.add_topics(["A", "B"])
    now = datetime.now()
    _set_due(store, "A", now - timedelta(days=2))
    _set_due(store, "B", now - timedelta(days=1))
    first = store.pick()["topic"]            # A (most overdue), pending
    # Simulate the pending being from a prior day, ungraded.
    data = store._load()
    data["pending"]["asked_at"] = (now - timedelta(days=1)).isoformat(timespec="seconds")
    store._save(data)
    second = store.pick()                    # A lapses → B surfaces
    assert first == "A"
    assert second["topic"] == "B"
    # A was rescheduled (no longer overdue) and NOT counted as seen.
    a = next(t for t in store._load()["topics"] if t["topic"] == "A")
    assert a["seen"] == 0 and a["due_at"] > now.isoformat()


def test_explore_lists_area_and_covered(store):
    store.set_area("deep learning")
    store.pick(topic="Attention")            # commit one (pending)
    store.grade("correct")                   # pushed to future → nothing due
    res = store.pick()
    assert res["mode"] == "explore"
    assert res["area"] == "deep learning"
    assert "Attention" in res["covered"]


def test_pick_with_topic_registers_and_pends(store):
    res = store.pick(topic="Transformers")
    assert res["mode"] == "ask"
    assert res["topic"] == "Transformers"
    assert store._load()["pending"]["topic"] == "Transformers"
    # Now gradeable.
    assert "error" not in store.grade("correct")


# ---- tool wrappers ------------------------------------------------------


def test_tool_wrappers_return_json(tmp_path, monkeypatch):
    import json
    monkeypatch.setenv("HOMUNCULUS_QUIZ_FILE", str(tmp_path / "quiz.json"))
    coach = load_real_tool_submodule("coach")

    # No topics, no area → explore mode that asks the agent to choose.
    QuizStore(tmp_path / "quiz.json").set_area("deep learning")
    explore = json.loads(coach.quiz_pick())
    assert explore["status"] == "explore"
    assert explore["area"] == "deep learning"

    # Agent commits a self-chosen sub-topic → registered + pending.
    picked = json.loads(coach.quiz_pick("Backprop"))
    assert picked["status"] == "picked"
    assert picked["topic"] == "Backprop"

    graded = json.loads(coach.quiz_grade("correct"))
    assert graded["box"] == 1
