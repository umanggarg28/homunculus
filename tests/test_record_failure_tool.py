"""record_failure tool — the terminal tool the prompts always referenced
but that was never registered.

The heartbeat prompt, AGENTS.md and TaskGuard all tell the agent to call
record_failure(task_id, reason) when it genuinely can't deliver; without
the tool, every such call errored with "tool does not exist" (observed
06-05 → 06-12). These pin that the wrapper now logs the failure through
the store the post-tick code already uses.
"""

from __future__ import annotations

import pytest

from tests.conftest import load_real_tool_submodule

load_real_tool_submodule("_state")
_sched = load_real_tool_submodule("scheduling")
from homunculus.tasks import TaskStore  # noqa: E402


@pytest.fixture()
def tasks_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMUNCULUS_TASKS_DIR", str(tmp_path))
    monkeypatch.setenv("HOMUNCULUS_USER_TZ_FILE", str(tmp_path / "tz.txt"))
    return tmp_path


def test_record_failure_logs_a_failure_run(tasks_dir):
    store = TaskStore(tasks_dir)
    task = store.create("Flaky job", "", None, "daily", True)
    out = _sched.record_failure(task["id"], "source returned 404")
    assert "Recorded failure" in out
    runs = store.get(task["id"])["last_runs"]
    assert runs[-1]["status"] == "failure"
    assert "404" in runs[-1]["result"]


def test_record_failure_unknown_task_errors(tasks_dir):
    out = _sched.record_failure("does-not-exist", "x")
    assert out.startswith("ERROR")


def test_record_failure_auto_cancels_after_repeated_failures(tasks_dir, monkeypatch):
    store = TaskStore(tasks_dir)
    task = store.create("Doomed", "", None, "daily", True)
    # Drive past the consecutive-failure limit.
    from homunculus.config import get_config
    limit = get_config().task.consecutive_failure_limit
    out = ""
    for _ in range(limit):
        out = _sched.record_failure(task["id"], "still broken")
    assert store.get(task["id"])["status"] == "cancelled"
    assert "auto-cancelled" in out
