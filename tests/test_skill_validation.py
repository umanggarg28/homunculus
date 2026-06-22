"""validate_skill_body — the structural gate that lets a weak model
rewrite its own skills without breaking them.

A proposal that fails these checks never reaches the approval queue, so
the checks ARE the safety boundary. Pin them: well-formed skills pass;
malformed frontmatter, stub bodies, wrong type, name mismatch, and
unknown forced-tool references all fail with a clear reason.
"""

from __future__ import annotations


from homunculus.skill_validation import validate_skill_body, validate_task_spec


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


REQUIRES = """---
name: skill_daily_brief
description: Morning brief with weather and HN.
type: skill
requires_tools:
  - get_weather
  - task_health_summary
---

# Daily brief — playbook

1. get_weather() for today's conditions.
2. task_health_summary() for commitments.
3. notify the brief.
"""


def test_requires_tools_all_present_passes():
    r = validate_skill_body(REQUIRES, known_tools={"get_weather", "task_health_summary", "notify"})
    assert r.ok, r.errors
    assert r.requires_tools == ["get_weather", "task_health_summary"]


def test_requires_tools_missing_rejected():
    # The morning-brief bug: a skill demanding a capability we don't have.
    r = validate_skill_body(REQUIRES, known_tools={"task_health_summary", "notify"})  # no get_weather
    assert not r.ok
    assert any("requires_tools" in e and "get_weather" in e for e in r.errors)


def test_requires_tools_not_a_list_rejected():
    body = "---\nname: skill_x_brief\ndescription: d\ntype: skill\nrequires_tools: get_weather\n---\nbody body body body body body body"
    r = validate_skill_body(body, known_tools={"get_weather"})
    assert not r.ok
    assert any("requires_tools" in e for e in r.errors)


def test_requires_tools_omitted_is_fine():
    r = validate_skill_body(GOOD, known_tools={"rss_feed", "notify"})
    assert r.ok
    assert r.requires_tools == []


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
    from homunculus.skill_validation import normalize_criteria
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
    from homunculus.skill_validation import normalize_criteria
    assert normalize_criteria(["notify_called", 5, None, {"type": "notify_unique"}]) == \
        [{"type": "notify_called"}, {"type": "notify_unique"}]


def test_normalize_compact_yaml_form():
    """A skill author's natural compact form maps to TaskGuard's canonical
    shape, with each value routed to the right param key."""
    from homunculus.skill_validation import normalize_criteria
    assert normalize_criteria([
        "notify_called",
        {"notify_min_chars": 200},
        {"notify_contains": "Hacker News AI Summary"},
        {"notify_matches": "https?://"},
        {"notify_unique": "leetcode\\.com/problems/([a-z0-9-]+)"},
    ]) == [
        {"type": "notify_called"},
        {"type": "notify_min_chars", "n": 200},
        {"type": "notify_contains", "text": "Hacker News AI Summary"},
        {"type": "notify_matches", "pattern": "https?://"},
        {"type": "notify_unique", "pattern": "leetcode\\.com/problems/([a-z0-9-]+)"},
    ]


def test_canonical_dict_passes_through_unchanged():
    from homunculus.skill_validation import normalize_criteria
    canonical = [{"type": "notify_min_chars", "n": 50}]
    assert normalize_criteria(canonical) == canonical


def test_skill_body_validates_success_criteria():
    """A skill declaring an unknown criterion is rejected; a valid compact
    one passes."""
    good = (
        "---\nname: skill_links\ndescription: d\ntype: skill\n"
        "success_criteria:\n  - notify_called\n  - notify_matches: 'https?://'\n---\n"
        "Step 1: do the thing. Step 2: notify with a link." + "x" * 40
    )
    assert validate_skill_body(good, expected_name="skill_links").ok

    bad = (
        "---\nname: skill_pop\ndescription: d\ntype: skill\n"
        "success_criteria:\n  - make_it_pop\n---\n"
        "Step 1: do the thing." + "x" * 40
    )
    res = validate_skill_body(bad, expected_name="skill_pop")
    assert not res.ok
    assert any("unknown success_criteria" in e for e in res.errors)
