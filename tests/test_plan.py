"""Plan store — the visible multi-step checklist."""

from __future__ import annotations

import importlib

import homunculus.plan as plan


def _fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMUNCULUS_TASKS_DIR", str(tmp_path))
    importlib.reload(plan)
    return plan


def test_set_and_render(tmp_path, monkeypatch):
    p = _fresh(tmp_path, monkeypatch)
    p.set_plan(["Search HN", "Fetch threads", "Summarise"])
    out = p.render(p.current())
    assert "- [ ] 1. Search HN" in out
    assert "- [ ] 3. Summarise" in out


def test_complete_marks_done_with_note(tmp_path, monkeypatch):
    p = _fresh(tmp_path, monkeypatch)
    p.set_plan(["A", "B"])
    p.complete(1, "found it")
    out = p.render(p.current())
    assert "- [x] 1. A — found it" in out
    assert "- [ ] 2. B" in out


def test_complete_out_of_range_returns_none(tmp_path, monkeypatch):
    p = _fresh(tmp_path, monkeypatch)
    p.set_plan(["only one"])
    assert p.complete(5) is None


def test_set_plan_drops_blank_steps(tmp_path, monkeypatch):
    p = _fresh(tmp_path, monkeypatch)
    result = p.set_plan(["real", "  ", ""])
    assert len(result) == 1


def test_render_empty_is_safe(tmp_path, monkeypatch):
    p = _fresh(tmp_path, monkeypatch)
    assert p.render(p.current()) == "(no active plan)"


def test_corrupt_file_treated_as_empty(tmp_path, monkeypatch):
    p = _fresh(tmp_path, monkeypatch)
    (tmp_path / "plan.json").write_text("{not json")
    assert p.current() == []
