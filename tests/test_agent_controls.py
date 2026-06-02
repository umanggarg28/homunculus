import json
from datetime import datetime, timezone

import agent_controls


def test_controls_normalize_and_persist(tmp_path):
    path = tmp_path / "agent_controls.json"

    saved = agent_controls.save_controls(
        {
            "max_steps": 500,
            "dry_run": True,
            "prefer_free_models": False,
            "allowed_tools": ["read_file", "read_file", ""],
            "blocked_tools": ["write_file"],
        },
        path=path,
    )

    assert saved.max_steps == 50
    assert saved.dry_run is True
    assert saved.prefer_free_models is False
    assert saved.allowed_tools == ["read_file"]
    assert agent_controls.load_controls(path).blocked_tools == ["write_file"]


def test_tool_block_reason_respects_allow_block_and_dry_run():
    controls = agent_controls.AgentControls(
        dry_run=True,
        allowed_tools=["read_file", "write_file"],
        blocked_tools=["delete_file"],
    )

    assert agent_controls.tool_block_reason("web_search", is_mutating=False, controls=controls)
    assert agent_controls.tool_block_reason("delete_file", is_mutating=True, controls=controls)
    assert agent_controls.tool_block_reason("write_file", is_mutating=True, controls=controls)
    assert agent_controls.tool_block_reason("read_file", is_mutating=False, controls=controls) is None


def test_agent_replay_groups_turns(monkeypatch, tmp_path):
    from transports import web_api

    events_path = tmp_path / "_events.jsonl"
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    records = [
        {"ts": now, "service": "web", "event": "user_message", "text": "do it"},
        {
            "ts": now,
            "service": "web",
            "event": "llm_call",
            "model": "gemini-2.5-flash",
            "host": "example.test",
            "input_tokens": 1000,
            "output_tokens": 100,
            "cached_tokens": 0,
            "request": "[]",
        },
        {"ts": now, "service": "web", "event": "tool_call", "name": "write_file", "args": "{}"},
        {"ts": now, "service": "web", "event": "tool_blocked", "name": "write_file", "result": "dry_run"},
        {"ts": now, "service": "web", "event": "assistant_reply", "text": "blocked for approval"},
    ]
    events_path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    monkeypatch.setattr(web_api, "EVENTS_PATH", events_path)

    turns = web_api._build_agent_replay(limit=3)

    assert len(turns) == 1
    assert turns[0]["user"] == "do it"
    assert turns[0]["assistant"] == "blocked for approval"
    assert turns[0]["models"][0]["model"] == "gemini-2.5-flash"
    assert turns[0]["tools"][0]["status"] == "blocked"
