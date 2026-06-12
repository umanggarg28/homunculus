"""Kill switch contract: paused persists through controls, and a paused
heartbeat tick is fully inert — it must return before touching task
state, memory, or any LLM machinery."""

import agent_controls
import heartbeat


def test_paused_round_trips_through_controls(tmp_path):
    path = tmp_path / "controls.json"
    saved = agent_controls.save_controls({"paused": True}, path=path)
    assert saved.paused is True
    assert agent_controls.load_controls(path=path).paused is True

    resumed = agent_controls.save_controls({"paused": False}, path=path)
    assert resumed.paused is False


def test_paused_defaults_false_and_survives_partial_updates(tmp_path):
    path = tmp_path / "controls.json"
    assert agent_controls.load_controls(path=path).paused is False

    agent_controls.save_controls({"paused": True}, path=path)
    # An unrelated update must not silently resume the agent.
    agent_controls.save_controls({"max_steps": 10}, path=path)
    assert agent_controls.load_controls(path=path).paused is True


def test_paused_tick_is_inert(monkeypatch):
    monkeypatch.setattr(
        agent_controls, "load_controls",
        lambda *a, **k: agent_controls.AgentControls(paused=True),
    )

    def _boom(*a, **k):  # any task-state access means the halt failed
        raise AssertionError("paused tick touched TaskStore")

    monkeypatch.setattr(heartbeat, "TaskStore", _boom)
    # memory=None is safe only because the paused branch returns first —
    # that's exactly the contract under test.
    heartbeat.tick(memory=None, model=None)
