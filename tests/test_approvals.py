"""Unit tests for the shared proposal resolver (homunculus.approvals).

These exercise the apply logic directly, with no web/transport surface, so the
single source of truth both the dashboard and chat commands call is proven on
its own. The web route's own tests (test_web_api) cover the HTTP translation.
"""

from __future__ import annotations

import pytest

from homunculus.approvals import ProposalError, ResolveResult, resolve_proposal
from homunculus.proposals import KIND_MEMORY_DELETE, KIND_NEW_SKILL, KIND_SKILL_EDIT, ProposalStore

_VALID_SKILL = """---
name: skill_demo_job
description: A demo job.
type: skill
states:
  - tool: notify
---

# Demo job — playbook

1. Compose a short, useful message for the operator about the demo job.
2. Call notify with that message so it is delivered to the user.
"""

_TOOLS = {"notify"}


@pytest.fixture()
def env(tmp_path):
    """A memory dir, tasks dir, and proposal store under tmp_path."""
    memory = tmp_path / "memory"
    tasks = tmp_path / "tasks"
    memory.mkdir()
    tasks.mkdir()
    store = ProposalStore(tmp_path / "proposals.json")
    return memory, tasks, store


def _resolve(env, proposal_id, action, **over):
    memory, tasks, store = env
    kwargs = dict(memory_dir=memory, tasks_dir=tasks, store=store, known_tools=_TOOLS)
    kwargs.update(over)
    return resolve_proposal(proposal_id, action, **kwargs)


def test_approve_new_skill_writes_versioned_skill(env):
    memory, _tasks, store = env
    p = store.create(kind=KIND_NEW_SKILL, skill_name="skill_demo_job", body=_VALID_SKILL)

    res = _resolve(env, p["id"], "approve")

    assert isinstance(res, ResolveResult) and res.ok
    assert res.action == "applied"
    assert res.detail["skill"] == "skill_demo_job" and res.detail["version"] == 1
    assert (memory / "skill_demo_job.md").exists()
    assert store.get(p["id"])["status"] == "approved"


def test_approve_new_skill_with_task_spec_creates_task(env):
    _memory, _tasks, store = env
    p = store.create(
        kind=KIND_NEW_SKILL, skill_name="skill_demo_job", body=_VALID_SKILL,
        task_spec={"title": "Demo task", "recurrence": "none",
                   "success_criteria": [{"type": "notify_called"}]},
    )

    res = _resolve(env, p["id"], "approve")

    assert res.detail["task"] is not None
    assert res.detail["task"]["title"] == "Demo task"
    assert res.detail["warning"] is None  # task linked → no orphan warning


def test_approve_skill_with_no_linked_task_warns_orphan(env):
    _memory, _tasks, store = env
    p = store.create(kind=KIND_SKILL_EDIT, skill_name="skill_demo_job", body=_VALID_SKILL)

    res = _resolve(env, p["id"], "approve")

    assert res.detail["warning"] is not None
    assert "NO task is linked" in res.detail["warning"]


def test_approve_memory_delete_forgets_the_file(env):
    memory, _tasks, store = env
    (memory / "project_old.md").write_text(
        "---\nname: Old\ndescription: stale\ntype: project\n---\n\nold.\n", encoding="utf-8"
    )
    p = store.create(
        kind=KIND_MEMORY_DELETE, skill_name="project_old.md", body="",
        validation={"target": "project_old.md"},
    )

    res = _resolve(env, p["id"], "approve")

    assert res.action == "deleted" and res.detail["memory"] == "project_old.md"
    assert not (memory / "project_old.md").exists()
    assert store.get(p["id"])["status"] == "approved"


def test_reject_marks_rejected_with_reason(env):
    _memory, _tasks, store = env
    p = store.create(kind=KIND_SKILL_EDIT, skill_name="skill_demo_job", body=_VALID_SKILL)

    res = _resolve(env, p["id"], "reject", reason="not useful")

    assert res.action == "rejected"
    assert res.detail == {"ok": True, "id": p["id"], "status": "rejected"}
    assert store.get(p["id"])["status"] == "rejected"


def test_missing_proposal_raises_404(env):
    with pytest.raises(ProposalError) as ei:
        _resolve(env, "prop-9999", "approve")
    assert ei.value.code == 404


def test_already_resolved_raises_409(env):
    _memory, _tasks, store = env
    p = store.create(kind=KIND_NEW_SKILL, skill_name="skill_demo_job", body=_VALID_SKILL)
    _resolve(env, p["id"], "approve")

    with pytest.raises(ProposalError) as ei:
        _resolve(env, p["id"], "approve")
    assert ei.value.code == 409  # idempotent-by-id: never applied twice


def test_invalid_memory_target_raises_400(env):
    _memory, _tasks, store = env
    p = store.create(
        kind=KIND_MEMORY_DELETE, skill_name="../escape.md", body="",
        validation={"target": "../escape.md"},
    )
    with pytest.raises(ProposalError) as ei:
        _resolve(env, p["id"], "approve")
    assert ei.value.code == 400


def test_protected_memory_target_raises_400(env):
    _memory, _tasks, store = env
    p = store.create(
        kind=KIND_MEMORY_DELETE, skill_name="MEMORY.md", body="",
        validation={"target": "MEMORY.md"},
    )
    with pytest.raises(ProposalError) as ei:
        _resolve(env, p["id"], "approve")
    assert ei.value.code == 400


def test_revalidation_failure_raises_422(env):
    _memory, _tasks, store = env
    p = store.create(kind=KIND_NEW_SKILL, skill_name="skill_demo_job", body=_VALID_SKILL)
    # notify is not in the catalogue now → the states: tool check fails.
    with pytest.raises(ProposalError) as ei:
        _resolve(env, p["id"], "approve", known_tools=set())
    assert ei.value.code == 422


def test_unknown_action_raises_400(env):
    _memory, _tasks, store = env
    p = store.create(kind=KIND_SKILL_EDIT, skill_name="skill_demo_job", body=_VALID_SKILL)
    with pytest.raises(ProposalError) as ei:
        _resolve(env, p["id"], "mutate")
    assert ei.value.code == 400
