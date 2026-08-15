"""Exercising a task must not require messaging the user.

Before this existed, the only way to run a task for real was to let it reach
the user's phone — verifying the delivery path meant delivering, and testing
the run-now fixes sent three unwanted notifications.

The suppression lives in the agent loop, after the pre-execute hook. It cannot
live in the tool (tools run in an MCP stdio subprocess that no in-process flag
reaches — the first attempt at this failed exactly there) and it cannot live in
the permission gate, which runs *before* the guard and would starve the success
criteria, making a rehearsal diverge from a real run.
"""

from __future__ import annotations

import sys


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


# ---- the suppression itself ---------------------------------------------


def _dispatch(monkeypatch, tool_name, suppressed):
    """Drive one real tool call through the loop's dispatch phase."""
    import homunculus.core as core
    import homunculus.tools as tools_module

    schema = {
        "type": "function",
        "function": {
            "name": tool_name,
            "description": "stub",
            "parameters": {"type": "object", "properties": {"text": {"type": "string"}}},
        },
    }
    executed: list[tuple[str, dict]] = []
    monkeypatch.setattr(tools_module, "SCHEMAS", [schema], raising=False)
    monkeypatch.setattr(
        tools_module, "execute",
        lambda n, a: (executed.append((n, a)), "sent for real")[1],
        raising=False,
    )

    observed: list[tuple[str, dict]] = []
    agent = core.Agent(
        memory=None,
        suppressed_tools=suppressed,
        pre_execute_hook=lambda n, a: observed.append((n, a)) or None,
    )
    call = {
        "id": "c1", "type": "function",
        "function": {"name": tool_name, "arguments": '{"text": "GitHub — quiet week"}'},
    }
    agent._dispatch_tool_calls([call], set(), {}, {}, [])
    return agent, executed, observed


def test_a_suppressed_tool_never_executes(monkeypatch):
    agent, executed, observed = _dispatch(monkeypatch, "notify", {"notify"})
    assert executed == [], "a rehearsal must not reach the tool"
    assert agent.suppressed_calls == [("notify", {"text": "GitHub — quiet week"})]


def test_the_guard_still_sees_a_suppressed_call(monkeypatch):
    """Suppression sits after the pre-execute hook so success criteria are
    evaluated exactly as in a real run. Before the hook, a rehearsal could
    never satisfy notify_called and would diverge from what it rehearses."""
    _, _, observed = _dispatch(monkeypatch, "notify", {"notify"})
    assert observed == [("notify", {"text": "GitHub — quiet week"})]


def test_an_unsuppressed_tool_still_runs(monkeypatch):
    agent, executed, _ = _dispatch(monkeypatch, "github_profile", {"notify"})
    assert executed and executed[0][0] == "github_profile"
    assert agent.suppressed_calls == []


def test_the_model_is_told_plainly_that_nothing_was_sent(monkeypatch):
    """Reporting success would teach the model that a rehearsal delivers, and
    the output guard would be right to call the resulting claim false."""
    agent, _, _ = _dispatch(monkeypatch, "notify", {"notify"})
    tool_replies = [m for m in agent.history if m.get("role") == "tool"]
    assert tool_replies, "the model must get a result for a suppressed call"
    assert "DRY RUN" in tool_replies[-1]["content"]
    assert "NOT executed" in tool_replies[-1]["content"]


def test_suppression_is_run_scoped(monkeypatch):
    """A rehearsal must not leak into a sibling Agent in the same process."""
    import homunculus.core as core
    dry = core.Agent(memory=None, suppressed_tools={"notify"})
    live = core.Agent(memory=None)
    assert dry._suppressed_tools == frozenset({"notify"})
    assert live._suppressed_tools == frozenset()
