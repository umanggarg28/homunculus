"""_plan_tick — skill selection + playbook injection for a heartbeat tick.

Regression for the silent playbook gap: skill bodies were injected into
the prompt ONLY when the skill declared `states:` frontmatter. A task
linked to an ordinary (stateless) skill ran with no playbook at all —
the model never saw its own instructions and improvised the task from
web_search (live 2026-06-11: delivered an algomap.io link for a task
whose playbook mandates LeetCode GraphQL).
"""

import sys
import types

if "tools.notify" not in sys.modules:
    _stub = types.ModuleType("tools.notify")
    _telegram_calls: list[str] = []
    _stub._send_to_telegram = lambda text: _telegram_calls.append(text) or None
    _stub._telegram_calls = _telegram_calls
    sys.modules["tools.notify"] = _stub

from heartbeat import _plan_tick  # noqa: E402


STATELESS_SKILL = """---
name: skill_daily_feed
description: How to deliver the daily feed
type: skill
---

# Daily feed playbook

1. Fetch from the canonical source only.
2. notify() the result.
"""

STATEFUL_SKILL = """---
name: skill_daily_feed_sm
description: State-machine variant
states:
  - tool: web_post
  - tool: notify
  - tool: complete_task
---

# State-machine playbook body
"""


def _task(task_id: str, skill: str | None) -> dict:
    return {"id": task_id, "title": task_id, "skill": skill}


def test_stateless_skill_playbook_is_injected(tmp_path):
    (tmp_path / "skill_daily_feed.md").write_text(STATELESS_SKILL, encoding="utf-8")
    states, selected, playbooks = _plan_tick(
        [_task("t1", "skill_daily_feed"), _task("t2", None)], tmp_path
    )
    assert states is None
    assert [t["id"] for t in selected] == ["t1", "t2"]   # nothing deferred
    assert len(playbooks) == 1
    assert "Daily feed playbook" in playbooks[0]
    assert "task 't1'" in playbooks[0]


def test_stateful_skill_runs_exclusively_with_its_playbook(tmp_path):
    (tmp_path / "skill_daily_feed_sm.md").write_text(STATEFUL_SKILL, encoding="utf-8")
    (tmp_path / "skill_daily_feed.md").write_text(STATELESS_SKILL, encoding="utf-8")
    states, selected, playbooks = _plan_tick(
        [_task("t1", "skill_daily_feed_sm"), _task("t2", "skill_daily_feed")], tmp_path
    )
    assert states is not None and [s["tool"] for s in states] == [
        "web_post", "notify", "complete_task",
    ]
    assert [t["id"] for t in selected] == ["t1"]          # t2 deferred
    assert len(playbooks) == 1 and "State-machine playbook body" in playbooks[0]


def test_missing_skill_falls_back_to_free_form(tmp_path):
    states, selected, playbooks = _plan_tick(
        [_task("t1", "skill_does_not_exist")], tmp_path
    )
    assert states is None
    assert [t["id"] for t in selected] == ["t1"]
    assert playbooks == []


def test_tasks_without_skills_have_no_playbooks(tmp_path):
    states, selected, playbooks = _plan_tick(
        [_task("t1", None), _task("t2", None)], tmp_path
    )
    assert states is None
    assert len(selected) == 2
    assert playbooks == []
