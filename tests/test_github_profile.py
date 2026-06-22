"""github_profile — weekly profile-health watcher.

Builds a stable text summary from the public GitHub API and diffs it
week-over-week through the shared snapshot machinery. The model reads
the diff and writes the message; it never counts JSON itself — so these
tests pin that the summary is stable (sorted, total-rolled-up) and that
the snapshot advances across runs.
"""

from __future__ import annotations


import pytest

from tests.conftest import load_real_tool_submodule

_helpers = load_real_tool_submodule("_helpers")
load_real_tool_submodule("web")
load_real_tool_submodule("watch")
_gh = load_real_tool_submodule("github")


def _profile(followers=10, public_repos=3):
    return {"login": "u", "followers": followers, "public_repos": public_repos}


def _repo(name, stars=0, forks=0, issues=0):
    return {
        "name": name,
        "stargazers_count": stars,
        "forks_count": forks,
        "open_issues_count": issues,
    }


@pytest.fixture()
def gh_env(tmp_path, monkeypatch):
    """Isolated snapshot dir + a stubbable GitHub API."""
    monkeypatch.setattr(_helpers, "CACHE_DIR", tmp_path)
    # Identity comes from config; keep tests deterministic regardless of
    # the host/CI environment.
    monkeypatch.delenv("HOMUNCULUS_GITHUB_USER", raising=False)

    def set_api(profile, repos):
        def fake_get_json(url: str):
            return repos if "/repos" in url else profile
        monkeypatch.setattr(_gh, "_get_json", fake_get_json)

    return set_api


# ---- summary shape ------------------------------------------------------


def test_first_run_reports_totals_and_top_repos(gh_env):
    gh_env(_profile(followers=42, public_repos=2), [
        _repo("homunculus", stars=9, forks=1, issues=2),
        _repo("dotfiles", stars=3),
    ])
    out = _gh.github_profile("umanggarg28")
    assert "FIRST SNAPSHOT" in out
    # Current snapshot is appended so absolute numbers are reportable
    # even with no prior week to diff against.
    assert "followers: 42" in out
    assert "total_stars: 12" in out
    assert "homunculus: 9 / 1 / 2" in out


def test_repos_sorted_by_stars_then_name(gh_env):
    gh_env(_profile(), [
        _repo("b", stars=1),
        _repo("a", stars=5),
        _repo("c", stars=5),
    ])
    out = _gh.github_profile("u")
    body = out.split("top repos")[1]
    # a and c tie at 5 → name breaks the tie (a before c); b last.
    assert body.index("a: 5") < body.index("c: 5") < body.index("b: 1")


# ---- week-over-week diff ------------------------------------------------


def test_star_gain_shows_up_as_change(gh_env):
    gh_env(_profile(), [_repo("homunculus", stars=9)])
    _gh.github_profile("u")  # baseline
    gh_env(_profile(), [_repo("homunculus", stars=12)])
    out = _gh.github_profile("u")
    assert out.startswith("CHANGED")
    assert "-  homunculus: 9 / 0 / 0" in out
    assert "+  homunculus: 12 / 0 / 0" in out


def test_no_change_when_metrics_identical(gh_env):
    gh_env(_profile(), [_repo("homunculus", stars=9)])
    _gh.github_profile("u")
    out = _gh.github_profile("u")
    assert out.startswith("NO CHANGE")


# ---- failure handling ---------------------------------------------------


def test_api_error_passes_through_and_is_not_snapshotted(gh_env, tmp_path, monkeypatch):
    monkeypatch.setattr(_gh, "_get_json", lambda url: "BLOCKED: GitHub API rate limit (60/hr unauthenticated).")
    out = _gh.github_profile("u")
    assert out.startswith("BLOCKED:")
    assert not (tmp_path / "watch" / "gh-profile-u.json").exists()


@pytest.mark.parametrize("bad", ["-leading", "has space", "a" * 40, "in--valid"])
def test_invalid_usernames_rejected(gh_env, bad):
    out = _gh.github_profile(bad)
    assert out.startswith("ERROR: invalid GitHub username")


def test_no_user_and_no_config_asks_instead_of_guessing(gh_env, monkeypatch):
    # Empty arg + nothing configured/learned: refuse and tell the agent
    # to ask the operator and remember — never guess.
    import sys
    import types
    fake_state = types.ModuleType("homunculus.tools._state")
    fake_state.get_memory = lambda: None
    monkeypatch.setitem(sys.modules, "homunculus.tools._state", fake_state)
    out = _gh.github_profile()
    assert out.startswith("ERROR: I don't know the operator's GitHub handle")
    assert "ASK the user" in out
    assert "update_world_state" in out


def test_learned_handle_in_world_state_is_used(gh_env, monkeypatch):
    import sys
    import types
    monkeypatch.delenv("HOMUNCULUS_GITHUB_USER", raising=False)
    fake_mem = type("M", (), {
        "world_state": type("W", (), {"read": lambda self: {"github_user": "umanggarg28"}})()
    })()
    fake_state = types.ModuleType("homunculus.tools._state")
    fake_state.get_memory = lambda: fake_mem
    monkeypatch.setitem(sys.modules, "homunculus.tools._state", fake_state)
    assert _gh.default_user() == "umanggarg28"


# ---- handle must be configured, never guessed --------------------------


def test_defaults_to_configured_handle_when_user_omitted(gh_env, monkeypatch):
    monkeypatch.setenv("HOMUNCULUS_GITHUB_USER", "umanggarg28")
    captured = {}
    gh_env(_profile(followers=1), [_repo("x", stars=1)])
    # Wrap snapshot_or_diff to capture which user was resolved.
    orig = _gh.snapshot_or_diff
    monkeypatch.setattr(_gh, "snapshot_or_diff", lambda user, summary: captured.setdefault("user", user) or orig(user, summary))
    _gh.github_profile()  # no argument
    assert captured["user"] == "umanggarg28"


def test_explicit_user_still_allowed_for_other_people(gh_env, monkeypatch):
    monkeypatch.setenv("HOMUNCULUS_GITHUB_USER", "umanggarg28")
    captured = {}
    gh_env(_profile(), [_repo("x")])
    orig = _gh.snapshot_or_diff
    monkeypatch.setattr(_gh, "snapshot_or_diff", lambda user, summary: captured.setdefault("user", user) or orig(user, summary))
    _gh.github_profile("torvalds")
    assert captured["user"] == "torvalds"
