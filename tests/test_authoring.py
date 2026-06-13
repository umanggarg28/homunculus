"""propose_skill tool — the agent's gate-respecting interface for
authoring its own skills.

It must never write to the registry directly: a valid proposal lands in
the pending queue, an invalid one bounces back with errors and files
nothing. Pin kind inference and the exists/not-exists guards so a "new"
proposal can't clobber an existing skill and an "edit" can't target a
missing one.
"""

from __future__ import annotations

import json

import pytest

from tests.conftest import load_real_tool_submodule

_authoring = load_real_tool_submodule("authoring")
from proposals import ProposalStore  # noqa: E402


VALID_BODY = """---
name: skill_summarize_hn
description: Summarize the top Hacker News AI posts each week.
type: skill
states:
  - tool: rss_feed
  - tool: notify
---

# Summarize HN — playbook

1. Call rss_feed on the configured Hacker News feed slug.
2. Read the '+' lines (new posts) and keep the AI-related ones.
3. notify a short digest with titles and links.
"""


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMUNCULUS_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("HOMUNCULUS_PROPOSALS_FILE", str(tmp_path / "proposals.json"))
    monkeypatch.setenv("HOMUNCULUS_USER_TZ_FILE", str(tmp_path / "tz.txt"))
    (tmp_path / "memory").mkdir()
    return tmp_path


def _store(env):
    return ProposalStore(env / "proposals.json")


def test_valid_new_skill_is_queued(env):
    out = json.loads(_authoring.propose_skill("skill_summarize_hn", VALID_BODY, "weekly HN digest"))
    assert out["ok"] is True
    assert out["status"] == "pending"
    assert out["kind"] == "new_skill"
    assert _store(env).pending_count() == 1


def test_invalid_body_returns_errors_and_files_nothing(env):
    out = json.loads(_authoring.propose_skill("skill_bad", "not a skill at all"))
    assert out["ok"] is False
    assert out["errors"]
    assert _store(env).pending_count() == 0


def test_new_skill_with_valid_task_is_queued(env):
    out = json.loads(_authoring.propose_skill(
        "skill_summarize_hn", VALID_BODY, "weekly digest",
        task={"title": "HN digest", "recurrence": "weekly",
              "success_criteria": [{"type": "notify_called"}]},
    ))
    assert out["ok"] is True
    assert _store(env).get(out["proposal_id"])["task_spec"]["title"] == "HN digest"


def test_new_skill_with_bad_task_bounces(env):
    out = json.loads(_authoring.propose_skill(
        "skill_summarize_hn", VALID_BODY, task={"recurrence": "fortnightly"},
    ))
    assert out["ok"] is False
    assert any("recurrence" in e or "title" in e for e in out["errors"])
    assert _store(env).pending_count() == 0


def test_edit_of_missing_skill_is_rejected(env):
    out = json.loads(_authoring.propose_skill("skill_ghost", VALID_BODY, kind="skill_edit"))
    assert out["ok"] is False
    assert any("no skill named" in e for e in out["errors"])


def test_new_skill_over_existing_is_rejected(env):
    # Seed an existing skill, then a 'new_skill' for the same name must bounce.
    from skills import Skills
    Skills(env / "memory").save("skill_summarize_hn", VALID_BODY, source="user-edit")
    out = json.loads(_authoring.propose_skill("skill_summarize_hn", VALID_BODY, kind="new_skill"))
    assert out["ok"] is False
    assert any("already exists" in e for e in out["errors"])


def test_kind_inferred_as_edit_for_existing_skill(env):
    from skills import Skills
    Skills(env / "memory").save("skill_summarize_hn", VALID_BODY, source="user-edit")
    out = json.loads(_authoring.propose_skill("skill_summarize_hn", VALID_BODY))
    assert out["ok"] is True
    assert out["kind"] == "skill_edit"


def test_task_on_edit_is_rejected(env):
    from skills import Skills
    Skills(env / "memory").save("skill_summarize_hn", VALID_BODY, source="user-edit")
    out = json.loads(_authoring.propose_skill(
        "skill_summarize_hn", VALID_BODY, kind="skill_edit",
        task={"title": "x", "recurrence": "daily"},
    ))
    assert out["ok"] is False
    assert any("only allowed on a new_skill" in e for e in out["errors"])
