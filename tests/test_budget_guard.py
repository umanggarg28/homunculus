"""Tests for the optional daily model budget guard."""

import json
import sys
import types
from datetime import datetime, timezone


def _make_tools_stub():
    mod = types.ModuleType("tools")
    mod.SCHEMAS = []
    mod.execute = lambda name, args: "ok"
    mod.get_mode = lambda: "build"
    mod.set_mode = lambda m: None
    mod.tool_names = lambda: set()
    return mod


sys.modules.setdefault("tools", _make_tools_stub())

events_stub = types.ModuleType("events")
events_stub.emit = lambda *a, **kw: None
events_stub.truncate_preview = lambda s, n=120: str(s)[:n]
events_stub.full_text = lambda s: str(s)
sys.modules.setdefault("events", events_stub)

dotenv_stub = types.ModuleType("dotenv")
dotenv_stub.load_dotenv = lambda *a, **kw: None
sys.modules.setdefault("dotenv", dotenv_stub)

tasks_stub = types.ModuleType("tasks")


class _FakeStore:
    def list(self, *a, **kw): return []
    def due(self): return []


tasks_stub.TaskStore = lambda *a, **kw: _FakeStore()
tasks_stub.ALLOWED_RECURRENCE = {"none", "daily", "weekly"}
sys.modules.setdefault("tasks", tasks_stub)

import core  # noqa: E402


def test_budget_guard_allows_free_models(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "ENFORCE_DAILY_BUDGET", True)
    monkeypatch.setenv("HOMUNCULUS_DAILY_BUDGET_USD", "0.01")
    monkeypatch.setenv("HOMUNCULUS_EVENTS_PATH", str(tmp_path / "_events.jsonl"))

    assert core._budget_blocks_model("some/model:free") is False


def test_budget_guard_blocks_known_paid_model_after_cap(monkeypatch, tmp_path):
    events = tmp_path / "_events.jsonl"
    events.write_text(
        json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "event": "llm_call",
            "model": "gemini-2.5-flash",
            "input_tokens": 1_000_000,
            "output_tokens": 0,
            "cached_tokens": 0,
        }) + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(core, "ENFORCE_DAILY_BUDGET", True)
    monkeypatch.setenv("HOMUNCULUS_DAILY_BUDGET_USD", "0.01")
    monkeypatch.setenv("HOMUNCULUS_EVENTS_PATH", str(events))

    assert core._budget_blocks_model("gemini-2.5-flash") is True


def test_budget_guard_does_not_block_when_disabled(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "ENFORCE_DAILY_BUDGET", False)
    monkeypatch.setenv("HOMUNCULUS_DAILY_BUDGET_USD", "0.01")
    monkeypatch.setenv("HOMUNCULUS_EVENTS_PATH", str(tmp_path / "_events.jsonl"))

    assert core._budget_blocks_model("gemini-2.5-flash") is False
