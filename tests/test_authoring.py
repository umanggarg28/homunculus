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
from homunculus.proposals import ProposalStore  # noqa: E402


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
    from homunculus.skills import Skills
    Skills(env / "memory").save("skill_summarize_hn", VALID_BODY, source="user-edit")
    out = json.loads(_authoring.propose_skill("skill_summarize_hn", VALID_BODY, kind="new_skill"))
    assert out["ok"] is False
    assert any("already exists" in e for e in out["errors"])


def test_kind_inferred_as_edit_for_existing_skill(env):
    from homunculus.skills import Skills
    Skills(env / "memory").save("skill_summarize_hn", VALID_BODY, source="user-edit")
    out = json.loads(_authoring.propose_skill("skill_summarize_hn", VALID_BODY))
    assert out["ok"] is True
    assert out["kind"] == "skill_edit"


def test_task_on_edit_is_rejected(env):
    from homunculus.skills import Skills
    Skills(env / "memory").save("skill_summarize_hn", VALID_BODY, source="user-edit")
    out = json.loads(_authoring.propose_skill(
        "skill_summarize_hn", VALID_BODY, kind="skill_edit",
        task={"title": "x", "recurrence": "daily"},
    ))
    assert out["ok"] is False
    assert any("only allowed on a new_skill" in e for e in out["errors"])


# ── surgical {old, new} edits (str_replace pattern) ──────────────────────

EMOJI_BODY = """---
name: skill_quiz
description: Daily quiz coach.
type: skill
states:
  - tool: notify
---

# Quiz — playbook

1. Pick a topic.
2. notify(text="🧠 Quiz — <question>").
   - If notify returns a timeout or error, retry once.
3. Done — emojis like 🧠 must survive an edit untouched.
"""


def _seed(env, body=EMOJI_BODY, name="skill_quiz"):
    from homunculus.skills import Skills
    Skills(env / "memory").save(name, body, source="user-edit")


def test_surgical_edit_changes_only_target_and_keeps_rest_verbatim(env):
    _seed(env)
    out = json.loads(_authoring.propose_skill(
        "skill_quiz", kind="skill_edit",
        edits=[{"old": "If notify returns a timeout or error, retry once.",
                "new": "Treat a DELIVERED result as success; only retry on ERROR."}],
        rationale="align with notify contract",
    ))
    assert out["ok"] is True, out
    body = _store(env).get(out["proposal_id"])["body"]
    assert "Treat a DELIVERED result as success" in body
    assert "timeout or error, retry once" not in body
    # untouched content — including the emoji — survives verbatim
    assert "🧠 Quiz — <question>" in body
    assert "emojis like 🧠 must survive" in body


def test_surgical_edit_deletes_with_empty_new(env):
    _seed(env)
    out = json.loads(_authoring.propose_skill(
        "skill_quiz", kind="skill_edit",
        edits=[{"old": "\n   - If notify returns a timeout or error, retry once.", "new": ""}],
    ))
    assert out["ok"] is True, out
    assert "retry once" not in _store(env).get(out["proposal_id"])["body"]


def test_surgical_edit_old_not_found_bounces(env):
    _seed(env)
    out = json.loads(_authoring.propose_skill(
        "skill_quiz", kind="skill_edit",
        edits=[{"old": "text that is not in the skill", "new": "x"}],
    ))
    assert out["ok"] is False
    assert any("not found" in e for e in out["errors"])
    assert _store(env).pending_count() == 0


def test_surgical_edit_ambiguous_match_bounces(env):
    _seed(env)
    out = json.loads(_authoring.propose_skill(
        "skill_quiz", kind="skill_edit",
        edits=[{"old": "🧠", "new": "🤖"}],  # appears twice
    ))
    assert out["ok"] is False
    assert any("matches" in e and "context" in e for e in out["errors"])


def test_surgical_edit_on_missing_skill_bounces(env):
    out = json.loads(_authoring.propose_skill(
        "skill_ghost", kind="skill_edit", edits=[{"old": "a", "new": "b"}],
    ))
    assert out["ok"] is False
    assert any("no skill named" in e for e in out["errors"])


def test_body_and_edits_together_bounces(env):
    _seed(env)
    out = json.loads(_authoring.propose_skill(
        "skill_quiz", VALID_BODY, kind="skill_edit",
        edits=[{"old": "Pick a topic.", "new": "Pick a hard topic."}],
    ))
    assert out["ok"] is False
    assert any("either a full body OR edits" in e for e in out["errors"])


def test_edit_with_neither_body_nor_edits_bounces(env):
    _seed(env)
    out = json.loads(_authoring.propose_skill("skill_quiz", kind="skill_edit"))
    assert out["ok"] is False
    assert any("full body, or edits" in e for e in out["errors"])


def test_edit_field_aliases_accepted(env):
    """A weak model may reach for old_str/new_str or old_string; accept them."""
    _seed(env)
    out = json.loads(_authoring.propose_skill(
        "skill_quiz", kind="skill_edit",
        edits=[{"old_str": "Pick a topic.", "new_str": "Pick a hard topic."}],
    ))
    assert out["ok"] is True, out
    assert "Pick a hard topic." in _store(env).get(out["proposal_id"])["body"]
