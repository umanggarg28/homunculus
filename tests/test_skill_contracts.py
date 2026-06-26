from __future__ import annotations

import pytest

from homunculus.skill_contracts import assert_registry_contracts, validate_registry_contracts


GOOD = """---
name: skill_daily_digest
description: Deliver a daily digest.
type: skill
requires_tools:
  - rss_feed
states:
  - tool: rss_feed
  - tool: notify
---

Read the configured feed, choose the most relevant entries, and notify the user
with a concise digest that includes grounded links and no placeholder content.
"""


def test_registry_contracts_accept_valid_skill(tmp_path):
    (tmp_path / "skill_daily_digest.md").write_text(GOOD, encoding="utf-8")

    issues = validate_registry_contracts(tmp_path, known_tools={"rss_feed", "notify"})

    assert issues == []


def test_registry_contracts_catch_filename_mismatch(tmp_path):
    (tmp_path / "skill_wrong_name.md").write_text(GOOD, encoding="utf-8")

    issues = validate_registry_contracts(tmp_path, known_tools={"rss_feed", "notify"})

    assert len(issues) == 1
    assert "does not match" in issues[0].message


def test_registry_contracts_catch_missing_tools(tmp_path):
    (tmp_path / "skill_daily_digest.md").write_text(GOOD, encoding="utf-8")

    issues = validate_registry_contracts(tmp_path, known_tools={"notify"})

    rendered = "\n".join(i.message for i in issues)
    assert "requires_tools" in rendered
    assert "states" in rendered


def test_assert_registry_contracts_raises_readable_failure(tmp_path):
    bad = GOOD.replace("type: skill", "type: project")
    (tmp_path / "skill_daily_digest.md").write_text(bad, encoding="utf-8")

    with pytest.raises(AssertionError) as exc:
        assert_registry_contracts(tmp_path, known_tools={"rss_feed", "notify"})

    assert "Skill contract failures" in str(exc.value)
    assert "skill_daily_digest" in str(exc.value)
