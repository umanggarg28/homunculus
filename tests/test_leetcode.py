"""Tests for the deterministic LeetCode next-problem tool.

The tool exists so the weak model never has to *find* the next problem (it
thrashed doing that live on 2026-06-30). These tests pin the determinism:
plan order is followed, the delivery ledger is respected, and the failure modes
return clear sentinels the skill can degrade on — all without touching the
network.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from tests.conftest import load_real_tool_submodule

# The tools package is stubbed in conftest; load the real leetcode submodule.
# It imports _task_store from scheduling lazily (inside _delivered_slugs), so
# the module loads here without dragging in the real tools package.
leetcode = load_real_tool_submodule("leetcode")


def _reset_cache():
    leetcode._mem_cache = None


def _graphql_response(slugs: list[tuple[str, str]]) -> MagicMock:
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {
        "data": {
            "studyPlanV2Detail": {
                "planSubGroups": [
                    {"questions": [
                        {"titleSlug": s, "questionFrontendId": fid,
                         "title": s.replace("-", " ").title(), "difficulty": "EASY"}
                        for fid, s in slugs
                    ]}
                ]
            }
        }
    }
    return r


# ---- fetch + parse ----------------------------------------------------


def test_fetch_parses_plan_order(monkeypatch):
    _reset_cache()
    resp = _graphql_response([("1", "two-sum"), ("88", "merge-sorted-array")])
    with patch.object(leetcode.httpx, "post", return_value=resp):
        out = leetcode._fetch_ordered_problems()
    assert [p["slug"] for p in out] == ["two-sum", "merge-sorted-array"]
    assert out[0]["id"] == "1" and out[0]["difficulty"] == "Easy"


def test_fetch_returns_empty_on_http_error(monkeypatch):
    _reset_cache()
    bad = MagicMock(status_code=503)
    with patch.object(leetcode.httpx, "post", return_value=bad):
        assert leetcode._fetch_ordered_problems() == []


# ---- selection is deterministic --------------------------------------


def test_next_skips_delivered_in_plan_order(monkeypatch):
    problems = [
        {"id": "1", "slug": "a", "title": "A", "difficulty": "Easy"},
        {"id": "2", "slug": "b", "title": "B", "difficulty": "Medium"},
        {"id": "3", "slug": "c", "title": "C", "difficulty": "Hard"},
    ]
    monkeypatch.setattr(leetcode, "_ordered_problems", lambda: problems)
    monkeypatch.setattr(leetcode, "_delivered_slugs", lambda task_id="": {"a"})

    out = leetcode.leetcode_next_problem()
    assert "slug: b" in out  # first undelivered in order, not "a" or "c"
    assert "https://leetcode.com/problems/b/" in out
    assert "2 of 3" in out  # remaining count


def test_all_delivered_sentinel(monkeypatch):
    problems = [{"id": "1", "slug": "a", "title": "A", "difficulty": "Easy"}]
    monkeypatch.setattr(leetcode, "_ordered_problems", lambda: problems)
    monkeypatch.setattr(leetcode, "_delivered_slugs", lambda task_id="": {"a"})
    assert leetcode.leetcode_next_problem().startswith("LEETCODE_ALL_DELIVERED")


def test_unavailable_sentinel_when_no_list(monkeypatch):
    monkeypatch.setattr(leetcode, "_ordered_problems", list)
    assert leetcode.leetcode_next_problem().startswith("LEETCODE_NEXT_UNAVAILABLE")


# ---- cache survives an API outage ------------------------------------


def test_disk_cache_fallback_on_outage(tmp_path, monkeypatch):
    _reset_cache()
    cache = tmp_path / "_leetcode_top150.json"
    cache.write_text(json.dumps({
        "fetched_at": 0.0,
        "problems": [{"id": "1", "slug": "cached", "title": "Cached", "difficulty": "Easy"}],
    }), encoding="utf-8")
    monkeypatch.setattr(leetcode, "_CACHE_PATH", cache)
    # API down → must fall back to the on-disk fetched copy, not fail.
    with patch.object(leetcode, "_fetch_ordered_problems", return_value=[]):
        out = leetcode._ordered_problems()
    assert [p["slug"] for p in out] == ["cached"]
