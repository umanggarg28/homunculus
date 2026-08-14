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
              "success_criteria": [{"type": "notify_called"},
                                   {"type": "notify_min_chars", "n": 200}]},
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


# --- a rejection must carry the text the retry needs -----------------------
#
# A surgical edit asks the model to reproduce an exact substring of the skill.
# When it cannot, a bare rejection leaves it nowhere to go: it does not have
# the text, so the retry is byte-identical and the loop never terminates.
# Production showed this as 214 stuck-loop events on propose_skill in one day.
# The tool already holds the body it is asking for, so it returns it.


def test_edit_without_edits_returns_the_current_body(env):
    _seed(env)
    out = json.loads(_authoring.propose_skill("skill_quiz", kind="skill_edit"))
    assert out["ok"] is False
    assert "current_body" in out
    assert "Pick a topic." in out["current_body"]
    assert "hint" in out


def test_old_not_found_returns_the_current_body(env):
    _seed(env)
    out = json.loads(_authoring.propose_skill(
        "skill_quiz", kind="skill_edit",
        edits=[{"old": "text that is not in the skill", "new": "x"}],
    ))
    assert out["ok"] is False
    assert "current_body" in out
    assert "Pick a topic." in out["current_body"]


def test_returned_body_lets_the_next_attempt_succeed(env):
    """The whole point: the retry can copy from what it was handed."""
    _seed(env)
    first = json.loads(_authoring.propose_skill("skill_quiz", kind="skill_edit"))
    body = first["current_body"]

    # Pick a line from the body the way the model would.
    lines = body.splitlines()
    fence = [i for i, line in enumerate(lines) if line.strip() == "---"][1]
    target = next(
        line for line in lines[fence + 1:]
        if len(line.strip()) > 10 and body.count(line) == 1
    )

    out = json.loads(_authoring.propose_skill(
        "skill_quiz", kind="skill_edit", rationale="apply it",
        edits=[{"old": target, "new": target + " Then stop."}],
    ))
    assert out["ok"] is True, out


def test_returned_body_is_bounded(env, monkeypatch):
    monkeypatch.setattr(_authoring, "_MAX_RETURNED_BODY_CHARS", 50)
    _seed(env)
    out = json.loads(_authoring.propose_skill("skill_quiz", kind="skill_edit"))
    assert len(out["current_body"]) < 120
    assert "truncated" in out["current_body"]


def test_missing_skill_rejection_carries_no_body(env):
    """Nothing to hand back when the skill does not exist."""
    out = json.loads(_authoring.propose_skill(
        "skill_ghost", kind="skill_edit", edits=[{"old": "a", "new": "b"}],
    ))
    assert out["ok"] is False
    assert "current_body" not in out


# --- criteria must be able to tell a delivery from a failure notice --------
#
# Production, 2026-08: a task declaring `notify_min_chars: 15` +
# `notify_contains: "Event watch"` recorded six consecutive days of success
# while delivering "Email not connected; cannot scan for events." Both
# criteria passed. The skill never looked broken and the outage stayed
# invisible. Shape validation cannot catch that; strength validation can.


def _task(criteria):
    return {"title": "T", "recurrence": "weekly", "success_criteria": criteria}


def test_notify_called_alone_is_rejected(env):
    out = json.loads(_authoring.propose_skill(
        "skill_summarize_hn", VALID_BODY, "r",
        task=_task([{"type": "notify_called"}]),
    ))
    assert out["ok"] is False
    assert any("cannot distinguish a delivery" in e for e in out["errors"])


def test_min_chars_below_the_floor_is_rejected(env):
    """The exact shape of the production failure."""
    out = json.loads(_authoring.propose_skill(
        "skill_summarize_hn", VALID_BODY, "r",
        task=_task([{"type": "notify_called"},
                    {"type": "notify_min_chars", "n": 15},
                    {"type": "notify_contains", "text": "Event watch"}]),
    ))
    assert out["ok"] is False
    assert any("below the" in e and "floor" in e for e in out["errors"])


def test_adequate_criteria_pass(env):
    out = json.loads(_authoring.propose_skill(
        "skill_summarize_hn", VALID_BODY, "r",
        task=_task([{"type": "notify_called"},
                    {"type": "notify_min_chars", "n": 200},
                    {"type": "notify_contains", "text": "Hacker News"}]),
    ))
    assert out["ok"] is True, out


def test_a_content_check_without_min_chars_is_enough(env):
    """The floor demands a content check, not specifically a length one."""
    out = json.loads(_authoring.propose_skill(
        "skill_summarize_hn", VALID_BODY, "r",
        task=_task([{"type": "notify_called"},
                    {"type": "notify_links_grounded"}]),
    ))
    assert out["ok"] is True, out


def test_compact_yaml_form_is_checked_too(env):
    """Authors write `- notify_min_chars: 15`; the floor must see through
    the compact form, not just the canonical dict."""
    out = json.loads(_authoring.propose_skill(
        "skill_summarize_hn", VALID_BODY, "r",
        task=_task(["notify_called", {"notify_min_chars": 15}]),
    ))
    assert out["ok"] is False
    assert any("floor" in e for e in out["errors"])


def test_non_integer_min_chars_is_reported(env):
    out = json.loads(_authoring.propose_skill(
        "skill_summarize_hn", VALID_BODY, "r",
        task=_task([{"type": "notify_min_chars", "n": "lots"}]),
    ))
    assert out["ok"] is False
    assert any("integer" in e for e in out["errors"])


def test_empty_criteria_are_left_alone(env):
    """A task spec with no criteria is a separate concern; the floor only
    judges criteria that exist."""
    from homunculus.skill_validation import criteria_strength_errors
    assert criteria_strength_errors([], where="t") == []
