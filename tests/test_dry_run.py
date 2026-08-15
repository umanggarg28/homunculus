"""Exercising a task must not require messaging the user.

Before this existed, the only way to run a task for real was to let it reach
the user's phone — verifying the delivery path meant delivering. These pin the
two properties that make a rehearsal useful: nothing leaves the process, and
the rehearsal cannot move the numbers a skill verdict is read from.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# Loaded standalone, the same way test_notify_channels does, so this does not
# clobber the tools.notify stub that other test modules install.
_spec = importlib.util.spec_from_file_location(
    "notify_dry_run_under_test",
    Path(__file__).parent.parent / "homunculus" / "tools" / "notify.py",
)
assert _spec and _spec.loader
notify = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(notify)


def _notify_module(monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(notify, "_channel_senders",
                        lambda: [("telegram", lambda t: sent.append(t) or None)])
    monkeypatch.setattr(notify, "_record_to_feed", lambda _t: True)
    return sent


def test_dry_run_reaches_no_channel(monkeypatch):
    sent = _notify_module(monkeypatch)
    with notify.dry_run() as outbox:
        result = notify.deliver("GitHub — quiet week")
    assert sent == [], "a rehearsal must not reach a real channel"
    assert outbox == ["GitHub — quiet week"]
    assert result["dry_run"] is True
    assert result["delivered"] == []


def test_delivery_resumes_after_the_block(monkeypatch):
    sent = _notify_module(monkeypatch)
    with notify.dry_run():
        notify.deliver("rehearsal")
    notify.deliver("the real thing")
    assert sent == ["the real thing"]


def test_the_agent_is_told_plainly_that_nothing_was_sent(monkeypatch):
    """Reporting success would teach the model that a dry run delivers, and
    the output guard would be right to call the resulting claim false."""
    _notify_module(monkeypatch)
    with notify.dry_run():
        text = notify._format_delivery("x", notify.deliver("x"))
    assert "DRY RUN" in text
    assert "NOT sent" in text


def test_nested_state_is_restored(monkeypatch):
    _notify_module(monkeypatch)
    assert notify.dry_run_active() is False
    with notify.dry_run():
        assert notify.dry_run_active() is True
    assert notify.dry_run_active() is False


def test_dry_run_survives_exit_in_another_context(monkeypatch):
    """The endpoint enters this around a streamed run; a generator resumed
    elsewhere cannot reset a ContextVar token created where it started."""
    import contextvars
    _notify_module(monkeypatch)

    def enter_and_exit():
        with notify.dry_run():
            contextvars.copy_context().run(lambda: None)

    contextvars.copy_context().run(enter_and_exit)
    assert notify.dry_run_active() is False


def test_a_rehearsal_is_excluded_from_the_scorecard():
    from homunculus import evals
    contract = evals.Contract(states=("notify",))
    runs = [
        {"ts": "t1", "status": "success", "tool_trace": "notify", "calls": 1},
        {"ts": "t2", "status": "success", "tool_trace": "notify", "calls": 1, "dry_run": True},
    ]
    card = evals.score_skill("t", contract, runs, [])
    assert card.runs == 1, "the rehearsal must not count toward the skill's record"


def test_mark_last_run_dry_flags_only_the_last_run(tmp_path):
    import importlib
    sys.modules.pop("homunculus.tasks", None)
    tasks = importlib.import_module("homunculus.tasks")
    store = tasks.TaskStore(tmp_path)
    t = store.create(title="x", recurrence="daily")
    store.complete(t["id"], "first")
    store.complete(t["id"], "second")
    store.mark_last_run_dry(t["id"])
    runs = store.get(t["id"])["last_runs"]
    assert runs[-1]["dry_run"] is True
    assert "dry_run" not in runs[-2]


def test_mark_last_run_dry_is_a_noop_without_runs(tmp_path):
    import importlib
    sys.modules.pop("homunculus.tasks", None)
    tasks = importlib.import_module("homunculus.tasks")
    store = tasks.TaskStore(tmp_path)
    t = store.create(title="x")
    store.mark_last_run_dry(t["id"])  # must not raise
    assert store.get(t["id"]).get("last_runs") == []
