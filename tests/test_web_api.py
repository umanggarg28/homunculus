"""
Tests for the FastAPI web_api endpoints.

Uses FastAPI's TestClient (backed by httpx) to hit the real route handlers
with temporary filesystem state instead of mocking the handlers themselves.
This tests the actual request/response logic — path parsing, JSON shape,
status codes, error cases — not just the helper functions.
"""

import importlib
import json
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def web_api(tmp_path, monkeypatch):
    """Import (or reload) transports.web_api with tmp dirs wired in."""
    # Ensure events is importable.
    if "events" not in sys.modules:
        ev = types.ModuleType("events")
        ev.emit = lambda *a, **k: None
        ev.full_text = lambda t: t
        ev.truncate_preview = lambda t, limit=200: t[:limit]
        sys.modules["events"] = ev

    # Ensure agent_controls is importable.
    if "agent_controls" not in sys.modules:
        import agent_controls  # noqa: F401

    # Restore the real tasks module so _task_store() uses the real TaskStore,
    # not the _FakeStore injected by test_schema_validation / test_output_guard.
    import importlib.util as _ilu
    _tasks_spec = _ilu.spec_from_file_location("tasks", Path(__file__).parent.parent / "tasks.py")
    _tasks_real = _ilu.module_from_spec(_tasks_spec)
    _tasks_spec.loader.exec_module(_tasks_real)
    sys.modules["tasks"] = _tasks_real

    # Point modules to tmp dirs via env vars BEFORE import.
    monkeypatch.setenv("HOMUNCULUS_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("HOMUNCULUS_TASKS_DIR", str(tmp_path / "tasks"))
    monkeypatch.setenv("HOMUNCULUS_PROPOSALS_FILE", str(tmp_path / "proposals.json"))
    monkeypatch.setenv("HOMUNCULUS_EVENTS_PATH", str(tmp_path / "events.jsonl"))
    monkeypatch.setenv("HOMUNCULUS_WEB_AUTH_TOKEN", "")
    monkeypatch.setenv("HOMUNCULUS_WEB_DIST", str(tmp_path / "dist"))

    (tmp_path / "memory").mkdir()
    (tmp_path / "tasks").mkdir()
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "assets").mkdir()

    # Always reload so module-level Path() and imports pick up fresh state.
    if "transports.web_api" in sys.modules:
        mod = importlib.reload(sys.modules["transports.web_api"])
    else:
        mod = importlib.import_module("transports.web_api")

    # Patch module globals to tmp paths (belt-and-suspenders).
    mod.MEMORY_DIR = tmp_path / "memory"
    mod.TASKS_DIR = tmp_path / "tasks"
    mod.EVENTS_PATH = tmp_path / "events.jsonl"
    mod._chat_memory = type("FakeMemory", (), {
        "root": tmp_path / "memory",
        "load_index": lambda self, **k: "",
        "load_core_block": lambda self: "",
        "save_session": lambda self, h: None,
        "load_session": lambda self: [],
        "clear_session": lambda self: None,
        "log_turn": lambda self, *a: None,
        # Sub-stores extracted from Memory in Bundle 2 #2. Each is a
        # tiny stub object exposing only the methods the web_api endpoints
        # actually call — full fakes would obscure what's used.
        "world_state": type("FakeWorldState", (), {
            "read": lambda self: {},
            "update": lambda self, updates: {},
            "clear": lambda self: None,
        })(),
        "next_tick": type("FakeNextTick", (), {
            "peek": lambda self: None,
        })(),
        "notifications": type("FakeNotifQ", (), {
            "queue": lambda self, text: None,
            "drain": lambda self: [],
        })(),
    })()

    return mod


@pytest.fixture()
def client(web_api):
    from starlette.testclient import TestClient
    return TestClient(web_api.app, raise_server_exceptions=True)


def _write_event(path: Path, **kwargs):
    ts = kwargs.pop("ts", datetime.now(timezone.utc).isoformat())
    rec = {"ts": ts, **kwargs}
    with path.open("a") as f:
        f.write(json.dumps(rec) + "\n")


# ---------------------------------------------------------------------------
# /api/config
# ---------------------------------------------------------------------------

def test_config_no_auth(client):
    resp = client.get("/api/config")
    assert resp.status_code == 200
    assert resp.json() == {"auth_required": False}


def test_config_with_auth(web_api, monkeypatch):
    monkeypatch.setattr(web_api, "WEB_AUTH_TOKEN", "secret123")
    from starlette.testclient import TestClient
    c = TestClient(web_api.app)
    resp = c.get("/api/config")
    assert resp.json()["auth_required"] is True


# ---------------------------------------------------------------------------
# /api/status
# ---------------------------------------------------------------------------

def test_status_empty_events(client):
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    for svc in ("heartbeat", "telegram", "web"):
        assert data[svc]["state"] == "unknown"
        assert data[svc]["last_seen"] is None


def test_status_live_service(client, web_api):
    ts = datetime.now(timezone.utc).isoformat()
    _write_event(web_api.EVENTS_PATH, event="service_ping", service="heartbeat", ts=ts)
    resp = client.get("/api/status")
    assert resp.json()["heartbeat"]["state"] == "live"
    assert resp.json()["telegram"]["state"] == "unknown"


def test_status_stale_service(client, web_api):
    from datetime import timedelta
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    _write_event(web_api.EVENTS_PATH, event="service_ping", service="telegram", ts=old_ts)
    resp = client.get("/api/status")
    assert resp.json()["telegram"]["state"] == "stale"


# ---------------------------------------------------------------------------
# /api/memory
# ---------------------------------------------------------------------------

def test_memory_list_empty(client):
    resp = client.get("/api/memory")
    assert resp.status_code == 200
    assert resp.json() == []


def test_memory_list_returns_entries(client, web_api):
    md = web_api.MEMORY_DIR / "user_test.md"
    md.write_text(
        "---\nname: test-user\ndescription: test entry\ntype: user\n---\n\nBody here.\n"
    )
    resp = client.get("/api/memory")
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "test-user"
    assert data[0]["type"] == "user"
    assert data[0]["filename"] == "user_test.md"


def test_memory_list_skips_system_files(client, web_api):
    (web_api.MEMORY_DIR / "MEMORY.md").write_text("# index\n")
    (web_api.MEMORY_DIR / "_next_tick.txt").write_text("2026-01-01T00:00\n")
    (web_api.MEMORY_DIR / "user_real.md").write_text(
        "---\nname: real\ndescription: real entry\ntype: user\n---\n\nBody.\n"
    )
    resp = client.get("/api/memory")
    names = [e["filename"] for e in resp.json()]
    assert "MEMORY.md" not in names
    assert "_next_tick.txt" not in names
    assert "user_real.md" in names


# ---------------------------------------------------------------------------
# /api/skills
# ---------------------------------------------------------------------------

def test_skills_empty(client):
    resp = client.get("/api/skills")
    assert resp.status_code == 200
    # tools.SCHEMAS is stubbed to [] so no skills
    assert resp.json() == []


def test_skills_event_aggregation(client, web_api, monkeypatch):
    monkeypatch.setattr(
        web_api.tools,
        "SCHEMAS",
        [{"function": {"name": "web_search", "description": "Search the web", "parameters": {}}}],
    )
    ts = datetime.now(timezone.utc).isoformat()
    _write_event(web_api.EVENTS_PATH, event="tool_call", name="web_search", ts=ts)
    _write_event(web_api.EVENTS_PATH, event="tool_result", name="web_search", result="ok", ts=ts)

    resp = client.get("/api/skills")
    data = resp.json()
    assert len(data) == 1
    skill = data[0]
    assert skill["name"] == "web_search"
    assert skill["call_count"] == 1
    assert skill["success_count"] == 1
    assert skill["failure_count"] == 0


def test_skills_failure_counted(client, web_api, monkeypatch):
    monkeypatch.setattr(
        web_api.tools,
        "SCHEMAS",
        [{"function": {"name": "web_fetch", "description": "Fetch URL", "parameters": {}}}],
    )
    ts = datetime.now(timezone.utc).isoformat()
    _write_event(web_api.EVENTS_PATH, event="tool_call", name="web_fetch", ts=ts)
    _write_event(
        web_api.EVENTS_PATH, event="tool_result", name="web_fetch", result="ERROR: 403", ts=ts
    )

    resp = client.get("/api/skills")
    skill = resp.json()[0]
    assert skill["failure_count"] == 1
    assert skill["success_count"] == 0


def test_skills_rate_skill_overlay(client, web_api, monkeypatch):
    """uses/consecutive_failures from skill_*.md appear in the response."""
    monkeypatch.setattr(
        web_api.tools,
        "SCHEMAS",
        [{"function": {"name": "my_skill", "description": "A learned skill", "parameters": {}}}],
    )
    skill_file = web_api.MEMORY_DIR / "skill_my_skill.md"
    skill_file.write_text(
        "---\nname: my_skill\ndescription: A learned skill\ntype: skill\n"
        "uses: 5\nconsecutive_failures: 2\n---\n\nBody.\n"
    )

    resp = client.get("/api/skills")
    skill = resp.json()[0]
    assert skill["uses"] == 5
    assert skill["consecutive_failures"] == 2


# ---------------------------------------------------------------------------
# /api/stats/today
# ---------------------------------------------------------------------------

def test_stats_today_empty(client):
    resp = client.get("/api/stats/today")
    assert resp.status_code == 200
    data = resp.json()
    assert data["cost_cents"] == 0


def test_stats_today_counts_llm_events(client, web_api):
    ts = datetime.now(timezone.utc).isoformat()
    _write_event(
        web_api.EVENTS_PATH,
        event="llm_call",
        service="web",
        ts=ts,
        model="gemini-2.5-flash",
        input_tokens=1000,
        output_tokens=200,
        cached_tokens=0,
    )
    resp = client.get("/api/stats/today")
    data = resp.json()
    # gemini-2.5-flash pricing: 15¢/1M in, 60¢/1M out
    # 1000 * 15/1M + 200 * 60/1M = 0.015 + 0.012 = 0.027¢
    assert data["cost_cents"] > 0
    assert data["events"] == 1
    assert data["input_tokens"] == 1000
    assert data["output_tokens"] == 200


# ---------------------------------------------------------------------------
# /api/tasks — CRUD
# ---------------------------------------------------------------------------

def test_tasks_list_empty(client):
    resp = client.get("/api/tasks")
    assert resp.status_code == 200
    assert resp.json() == []


def test_tasks_create_and_list(client):
    resp = client.post(
        "/api/tasks",
        json={"title": "Test task", "description": "Do the thing", "recurrence": "none"},
    )
    assert resp.status_code == 200
    task = resp.json()
    assert task["title"] == "Test task"
    assert task["status"] == "active"
    task_id = task["id"]

    list_resp = client.get("/api/tasks?status=active")
    ids = [t["id"] for t in list_resp.json()]
    assert task_id in ids


def test_tasks_create_requires_title(client):
    resp = client.post("/api/tasks", json={"description": "no title"})
    assert resp.status_code == 400


def test_tasks_complete(client):
    create = client.post("/api/tasks", json={"title": "Finish me"})
    task_id = create.json()["id"]

    resp = client.post(f"/api/tasks/{task_id}/complete", json={"result": "all done"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"


def test_tasks_cancel(client):
    create = client.post("/api/tasks", json={"title": "Cancel me"})
    task_id = create.json()["id"]

    resp = client.post(f"/api/tasks/{task_id}/cancel", json={"reason": "not needed"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"


def test_tasks_delete(client):
    create = client.post("/api/tasks", json={"title": "Delete me"})
    task_id = create.json()["id"]

    del_resp = client.delete(f"/api/tasks/{task_id}")
    assert del_resp.status_code == 200
    assert del_resp.json() == {"ok": True}

    list_resp = client.get("/api/tasks?status=all")
    ids = [t["id"] for t in list_resp.json()]
    assert task_id not in ids


def test_tasks_complete_not_found(client):
    resp = client.post("/api/tasks/nonexistent/complete", json={})
    assert resp.status_code == 404


def test_tasks_update(client):
    create = client.post("/api/tasks", json={"title": "Old title"})
    task_id = create.json()["id"]

    resp = client.patch(f"/api/tasks/{task_id}", json={"title": "New title"})
    assert resp.status_code == 200
    assert resp.json()["title"] == "New title"


def test_tasks_invalid_recurrence(client):
    resp = client.post(
        "/api/tasks", json={"title": "Bad recurrence", "recurrence": "hourly"}
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Auth enforcement
# ---------------------------------------------------------------------------

def test_auth_rejects_bad_token(web_api, monkeypatch):
    monkeypatch.setattr(web_api, "WEB_AUTH_TOKEN", "correct-token")
    from starlette.testclient import TestClient
    c = TestClient(web_api.app)
    resp = c.get("/api/status", headers={"X-Homunculus-Token": "wrong-token"})
    assert resp.status_code == 401


def test_auth_accepts_correct_token(web_api, monkeypatch):
    monkeypatch.setattr(web_api, "WEB_AUTH_TOKEN", "correct-token")
    from starlette.testclient import TestClient
    c = TestClient(web_api.app)
    resp = c.get("/api/status", headers={"X-Homunculus-Token": "correct-token"})
    assert resp.status_code == 200


def test_auth_accepts_query_token(web_api, monkeypatch):
    monkeypatch.setattr(web_api, "WEB_AUTH_TOKEN", "correct-token")
    from starlette.testclient import TestClient
    c = TestClient(web_api.app)
    resp = c.get("/api/status?token=correct-token")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# /api/proposals — agent self-authoring, human-gated approval
# ---------------------------------------------------------------------------

_VALID_NEW_SKILL = """---
name: skill_demo_job
description: A demo job.
type: skill
states:
  - tool: notify
---

# Demo job — playbook

1. Compose a short, useful message for the operator about the demo job.
2. Call notify with that message so it is delivered to the user.
3. Keep it under a few lines; this is a glance, not a report.
"""


def _file_proposal(web_api, **over):
    """Create a pending proposal directly in the store the API reads."""
    import os
    from proposals import ProposalStore
    store = ProposalStore(os.environ["HOMUNCULUS_PROPOSALS_FILE"])
    kwargs = dict(
        kind="new_skill", skill_name="skill_demo_job", body=_VALID_NEW_SKILL,
        rationale="demo",
    )
    kwargs.update(over)
    return store.create(**kwargs)


def test_proposals_list_empty(client):
    assert client.get("/api/proposals").json() == []


def test_approve_new_skill_writes_versioned_skill(client, web_api):
    web_api.tools.SCHEMAS = [{"function": {"name": "notify"}}]
    p = _file_proposal(web_api)
    resp = client.post(f"/api/proposals/{p['id']}/approve")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] and body["skill"] == "skill_demo_job" and body["version"] == 1
    # Skill file actually landed in the registry.
    assert (web_api.MEMORY_DIR / "skill_demo_job.md").exists()
    # And it's gone from pending.
    assert client.get("/api/proposals?status=pending").json() == []
    assert len(client.get("/api/proposals?status=approved").json()) == 1


def test_approve_new_skill_with_task_creates_task(client, web_api):
    web_api.tools.SCHEMAS = [{"function": {"name": "notify"}}]
    p = _file_proposal(web_api, task_spec={
        "title": "Demo job", "recurrence": "daily",
        "success_criteria": [{"type": "notify_called"}],
    })
    resp = client.post(f"/api/proposals/{p['id']}/approve")
    assert resp.status_code == 200, resp.text
    assert resp.json()["task"]["skill"] == "skill_demo_job"
    tasks = client.get("/api/tasks?status=active").json()
    assert any(t["skill"] == "skill_demo_job" for t in tasks)


def test_approve_revalidates_against_live_tools(client, web_api):
    # notify is NOT in the catalogue now → the states: tool check fails.
    web_api.tools.SCHEMAS = [{"function": {"name": "web_fetch"}}]
    p = _file_proposal(web_api)
    resp = client.post(f"/api/proposals/{p['id']}/approve")
    assert resp.status_code == 422
    assert "don't exist" in resp.text


def test_reject_marks_rejected(client, web_api):
    p = _file_proposal(web_api)
    resp = client.post(f"/api/proposals/{p['id']}/reject", json={"reason": "not useful"})
    assert resp.status_code == 200
    assert client.get("/api/proposals?status=rejected").json()[0]["resolution_note"] == "not useful"


def test_approve_missing_proposal_404(client):
    assert client.post("/api/proposals/prop-9999/approve").status_code == 404


def test_double_approve_conflict(client, web_api):
    web_api.tools.SCHEMAS = [{"function": {"name": "notify"}}]
    p = _file_proposal(web_api)
    assert client.post(f"/api/proposals/{p['id']}/approve").status_code == 200
    assert client.post(f"/api/proposals/{p['id']}/approve").status_code == 409


# ---------------------------------------------------------------------------
# /api/input-expected — drives the CHAT "your turn" sidebar badge
# ---------------------------------------------------------------------------


def test_input_expected_reflects_pending_quiz(client, tmp_path, monkeypatch):
    qf = tmp_path / "quiz.json"
    monkeypatch.setenv("HOMUNCULUS_QUIZ_FILE", str(qf))

    qf.write_text(json.dumps({"area": "deep learning", "topics": [], "pending": None}))
    r = client.get("/api/input-expected").json()
    assert r["expected"] is False

    # A DELIVERED pending → your turn.
    qf.write_text(json.dumps({
        "area": "deep learning", "topics": [],
        "pending": {"topic": "attention", "asked_at": "2026-06-16T20:00:00", "delivered": True},
    }))
    r = client.get("/api/input-expected").json()
    assert r["expected"] is True
    assert r["reason"] == "quiz"
    assert r["detail"] == "attention"

    # A pending whose delivery FAILED (delivered False / missing) must NOT light
    # the badge — the user never saw the question (the notify-timeout case).
    qf.write_text(json.dumps({
        "area": "deep learning", "topics": [],
        "pending": {"topic": "attention", "asked_at": "2026-06-16T20:00:00", "delivered": False},
    }))
    assert client.get("/api/input-expected").json()["expected"] is False

    qf.write_text(json.dumps({
        "area": "deep learning", "topics": [],
        "pending": {"topic": "attention", "asked_at": "2026-06-16T20:00:00"},  # legacy orphan, no flag
    }))
    assert client.get("/api/input-expected").json()["expected"] is False
