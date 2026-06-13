"""validate_skill_body — the structural gate that lets a weak model
rewrite its own skills without breaking them.

A proposal that fails these checks never reaches the approval queue, so
the checks ARE the safety boundary. Pin them: well-formed skills pass;
malformed frontmatter, stub bodies, wrong type, name mismatch, and
unknown forced-tool references all fail with a clear reason.
"""

from __future__ import annotations

import pytest

from skill_validation import validate_skill_body, validate_task_spec


GOOD = """---
name: skill_summarize_hn
description: Summarize the top Hacker News AI posts.
type: skill
states:
  - tool: rss_feed
  - tool: notify
---

# Summarize HN — playbook

1. Call rss_feed on the HN feed.
2. Pick the AI-related posts.
3. notify a short digest.
"""


def test_well_formed_skill_passes():
    r = validate_skill_body(GOOD, known_tools={"rss_feed", "notify"})
    assert r.ok, r.errors
    assert r.frontmatter["name"] == "skill_summarize_hn"
    assert r.states_tools == ["rss_feed", "notify"]


def test_missing_frontmatter_fails():
    r = validate_skill_body("# just a heading\n\nsome text that is long enough to pass body length easily")
    assert not r.ok
    assert any("frontmatter" in e for e in r.errors)


def test_unclosed_frontmatter_fails():
    r = validate_skill_body("---\nname: skill_x\n(no closing fence)")
    assert not r.ok
    assert any("not closed" in e for e in r.errors)


def test_invalid_yaml_fails():
    r = validate_skill_body("---\nname: : : :\n---\nbody body body body body body body body body")
    assert not r.ok
    assert any("YAML" in e for e in r.errors)


def test_bad_name_rejected():
    body = GOOD.replace("name: skill_summarize_hn", "name: Summarize HN")
    r = validate_skill_body(body)
    assert not r.ok
    assert any("skill_<slug>" in e for e in r.errors)


def test_name_mismatch_when_editing_rejected():
    r = validate_skill_body(GOOD, expected_name="skill_something_else")
    assert not r.ok
    assert any("retarget" in e for e in r.errors)


def test_wrong_type_rejected():
    body = GOOD.replace("type: skill", "type: project")
    r = validate_skill_body(body)
    assert not r.ok
    assert any("type" in e for e in r.errors)


def test_stub_body_rejected():
    body = "---\nname: skill_x\ndescription: x\ntype: skill\n---\ndo it"
    r = validate_skill_body(body)
    assert not r.ok
    assert any("too short" in e for e in r.errors)


def test_states_referencing_unknown_tool_rejected():
    r = validate_skill_body(GOOD, known_tools={"notify"})  # rss_feed missing
    assert not r.ok
    assert any("don't exist" in e for e in r.errors)


def test_states_not_a_list_rejected():
    body = GOOD.replace("states:\n  - tool: rss_feed\n  - tool: notify", "states: just_a_string")
    r = validate_skill_body(body, known_tools={"rss_feed", "notify"})
    assert not r.ok


def test_known_tools_omitted_skips_tool_check():
    # Pure-logic mode: don't fail just because we didn't pass the registry.
    r = validate_skill_body(GOOD)
    assert r.ok, r.errors


# ---- task spec ----------------------------------------------------------


def test_valid_task_spec_passes():
    assert validate_task_spec({
        "title": "Summarize HN",
        "recurrence": "weekly",
        "success_criteria": [{"type": "notify_called"}, {"type": "notify_contains", "text": "HN"}],
    }) == []


def test_task_spec_needs_title():
    errs = validate_task_spec({"recurrence": "daily"})
    assert any("title" in e for e in errs)


def test_oneshot_task_needs_due_at():
    errs = validate_task_spec({"title": "x", "recurrence": "none"})
    assert any("due_at" in e for e in errs)


def test_unknown_criterion_rejected():
    errs = validate_task_spec({
        "title": "x", "recurrence": "daily",
        "success_criteria": [{"type": "make_it_good"}],
    })
    assert any("unknown success_criteria" in e for e in errs)


# ---- criteria normalization (models often write bare strings) ----------


def test_string_criteria_are_accepted_and_normalized():
    from skill_validation import normalize_criteria
    assert validate_task_spec({
        "title": "x", "recurrence": "daily",
        "success_criteria": ["notify_called", "notify_has_code"],
    }) == []
    assert normalize_criteria(["notify_called"]) == [{"type": "notify_called"}]


def test_unknown_string_criterion_still_rejected():
    errs = validate_task_spec({
        "title": "x", "recurrence": "daily", "success_criteria": ["make_it_good"],
    })
    assert any("unknown success_criteria" in e for e in errs)


def test_normalize_drops_non_str_non_dict():
    from skill_validation import normalize_criteria
    assert normalize_criteria(["notify_called", 5, None, {"type": "notify_unique"}]) == \
        [{"type": "notify_called"}, {"type": "notify_unique"}]
