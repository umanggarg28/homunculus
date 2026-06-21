"""
Tests for provider-chain reliability fixes:
  1. RE_FIRE_SUPPRESSION_SECONDS (duplicate-notification prevention)
  2. User-Agent header on all httpx calls
  3. MODEL_FALLBACK doesn't include hermes-3
  4. TaskStore edge cases: malformed dates, empty files, concurrent writes
  5. Provider slot: empty key skips the slot
"""

import importlib.util
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pytest


def _load_real_tasks():
    """Load tasks.py directly by path, bypassing any sys.modules stub."""
    spec = importlib.util.spec_from_file_location("tasks_real", Path(__file__).parent.parent / "homunculus" / "tasks.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_tasks = _load_real_tasks()
# The constant moved into HomunculusConfig.task during the agent
# refactor. Keep the local alias so the rest of this test file reads
# the same way.
RE_FIRE_SUPPRESSION_SECONDS = _tasks.get_config().task.re_fire_suppression_seconds
TaskStore = _tasks.TaskStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _store(tmp_path: Path) -> TaskStore:
    return TaskStore(tmp_path / "tasks")


def _add_overdue(store: TaskStore, title: str = "t") -> dict:
    past = (datetime.now() - timedelta(minutes=5)).isoformat(timespec="seconds")
    return store.create(title=title, due_at=past)


# ---------------------------------------------------------------------------
# RE_FIRE_SUPPRESSION_SECONDS must be >= 30 min
# ---------------------------------------------------------------------------

def test_refire_suppression_is_at_least_30_minutes():
    assert RE_FIRE_SUPPRESSION_SECONDS >= 30 * 60, (
        "Suppression window too short — provider outages cause duplicate notifications. "
        f"Current value: {RE_FIRE_SUPPRESSION_SECONDS}s"
    )


# ---------------------------------------------------------------------------
# TaskStore.due() — core scheduling logic
# ---------------------------------------------------------------------------

def test_due_returns_overdue_task(tmp_path):
    store = _store(tmp_path)
    _add_overdue(store, "gym")
    assert len(store.due()) == 1


def test_due_suppresses_recently_fired(tmp_path):
    store = _store(tmp_path)
    task = _add_overdue(store)
    store.mark_fired(task["id"])
    assert store.due() == []


def test_due_reappears_after_suppression_window(tmp_path):
    store = _store(tmp_path)
    task = _add_overdue(store)
    store.mark_fired(task["id"])
    # Wind last_fired_at back past the window; clear executing so due() sees the task
    tasks = store.all()
    tasks[0]["last_fired_at"] = (
        datetime.now() - timedelta(seconds=RE_FIRE_SUPPRESSION_SECONDS + 60)
    ).isoformat(timespec="seconds")
    tasks[0]["executing"] = False
    store._write(tasks)
    assert len(store.due()) == 1


def test_mark_fired_twice_keeps_suppressed(tmp_path):
    store = _store(tmp_path)
    task = _add_overdue(store)
    store.mark_fired(task["id"])
    store.mark_fired(task["id"])
    assert store.due() == []


def test_future_task_not_due(tmp_path):
    store = _store(tmp_path)
    future = (datetime.now() + timedelta(hours=1)).isoformat(timespec="seconds")
    store.create(title="future", due_at=future)
    assert store.due() == []


def test_malformed_due_at_skipped(tmp_path):
    """Tasks with unparseable due_at must not crash due()."""
    store = _store(tmp_path)
    _add_overdue(store)
    tasks = store.all()
    tasks[0]["due_at"] = "not-a-date"
    store._write(tasks)
    # Should return empty, not raise
    assert store.due() == []


def test_missing_due_at_skipped(tmp_path):
    """Tasks with no due_at must not crash due()."""
    store = _store(tmp_path)
    _add_overdue(store)
    tasks = store.all()
    del tasks[0]["due_at"]
    store._write(tasks)
    assert store.due() == []


def test_completed_task_not_due(tmp_path):
    """Completed tasks must never re-fire."""
    store = _store(tmp_path)
    task = _add_overdue(store)
    store.complete(task["id"], result="done")
    assert store.due() == []


def test_multiple_tasks_only_overdue_returned(tmp_path):
    store = _store(tmp_path)
    _add_overdue(store, "overdue")
    future = (datetime.now() + timedelta(hours=1)).isoformat(timespec="seconds")
    store.create(title="future", due_at=future)
    due = store.due()
    assert len(due) == 1
    assert due[0]["title"] == "overdue"


# ---------------------------------------------------------------------------
# Adversarial: corrupt / empty tasks file
# ---------------------------------------------------------------------------

def test_corrupt_tasks_file_does_not_crash(tmp_path):
    store = _store(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{ this is not valid json }")
    assert store.list("active") == []


def test_empty_tasks_file_does_not_crash(tmp_path):
    store = _store(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("")
    assert store.list("active") == []


def test_due_on_corrupt_file_does_not_crash(tmp_path):
    store = _store(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("!!!garbage!!!")
    assert store.due() == []


# ---------------------------------------------------------------------------
# Adversarial: concurrent mark_fired doesn't corrupt the task file
# ---------------------------------------------------------------------------

def test_concurrent_mark_fired_does_not_corrupt(tmp_path):
    store = _store(tmp_path)
    task = _add_overdue(store)

    errors = []

    def fire():
        try:
            store.mark_fired(task["id"])
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=fire) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Concurrent mark_fired raised: {errors}"
    tasks = store.all()
    assert len(tasks) == 1


# ---------------------------------------------------------------------------
# User-Agent header present in every httpx.post call
# ---------------------------------------------------------------------------

def test_user_agent_constant_exists():
    from homunculus import core
    assert hasattr(core, "_HTTP_HEADERS_BASE"), "_HTTP_HEADERS_BASE missing from core.py"
    assert "User-Agent" in core._HTTP_HEADERS_BASE


def test_user_agent_value_not_empty():
    from homunculus import core
    assert core._HTTP_HEADERS_BASE["User-Agent"].strip()


def test_all_httpx_calls_use_header_base():
    """Every httpx.post call in core.py must spread _HTTP_HEADERS_BASE into headers."""
    import ast
    import pathlib
    src = pathlib.Path("homunculus/core.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "post"):
            continue
        for kw in node.keywords:
            if kw.arg == "headers":
                val = kw.value
                if isinstance(val, ast.Dict):
                    has_spread = any(k is None for k in val.keys)
                    assert has_spread, (
                        f"httpx.post at line {node.lineno} has headers= dict "
                        f"without **_HTTP_HEADERS_BASE"
                    )


# ---------------------------------------------------------------------------
# MODEL_FALLBACK must not contain hermes-3 (no tool calling on free tier)
# ---------------------------------------------------------------------------

def test_model_fallback_excludes_hermes():
    from homunculus import core
    assert "hermes" not in core.MODEL_FALLBACK.lower(), (
        "hermes-3-405b:free does not support tool calling — remove from MODEL_FALLBACK"
    )


def test_model_fallback_contains_verified_models():
    from homunculus import core
    verified = [
        "meta-llama/llama-3.3-70b-instruct",
        "openai/gpt-oss-120b",
        "moonshotai/kimi-k2.6",
        "qwen/qwen3-coder",
    ]
    assert any(m in core.MODEL_FALLBACK for m in verified), (
        f"MODEL_FALLBACK '{core.MODEL_FALLBACK}' has none of the verified tool-calling models"
    )


# ---------------------------------------------------------------------------
# Provider slot: empty API key must be excluded
# ---------------------------------------------------------------------------

def test_empty_api_key_slot_is_skipped(monkeypatch):
    """A slot with an empty key must contribute no (url, '', model) tuples.

    Checking only `url not in urls` was too strict — when two slots
    share the same URL (e.g. primary AND fallback both on OpenRouter,
    one paid + one free-tier pool), the URL legitimately appears via
    the slot that has a key. What matters is no provider tuple goes
    out with an empty key string.
    """
    from homunculus import core
    monkeypatch.setenv("HOMUNCULUS_API_KEY_FALLBACK", "")
    slots = core._providers("some-model")
    empty_key_entries = [(u, k, m) for u, k, m in slots if not k]
    assert not empty_key_entries, (
        f"Provider slots with empty keys must be filtered out, got: {empty_key_entries}"
    )
