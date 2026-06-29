"""github_profile — weekly profile-health snapshot for a public user.

Umang cares about the recruiter-visible surface (merged PRs, stars), so
the agent watches it: stars, forks, followers, open issues across public
repos, distilled into a stable summary and diffed week-over-week through
the shared snapshot machinery (tools/watch.snapshot_diff). The model
reads the diff and writes the "+3 stars on homunculus this week"
message; it never counts the JSON itself.

Uses the unauthenticated GitHub REST API (60 req/hr/IP — a weekly task
spends 2). No token, no secrets, public data only.
"""

from __future__ import annotations

import json
import os
import re

import httpx

from . import watch
from .web import _url_block_reason

_API = "https://api.github.com"
_USER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")
# Cap the repos we itemise so the summary (and its diff) stays a glance,
# not a wall. Repos beyond this still count toward totals.
_TOP_REPOS = 15


def _get_json(url: str):
    """GET + parse JSON through the SSRF guard. Returns parsed data or
    an ERROR:/BLOCKED: string (never raises)."""
    if (reason := _url_block_reason(url)) is not None:
        return reason
    try:
        resp = httpx.get(
            url,
            timeout=30.0,
            follow_redirects=False,
            headers={
                "User-Agent": "Mozilla/5.0 (Homunculus AI assistant)",
                "Accept": "application/vnd.github+json",
            },
        )
    except httpx.HTTPError as e:
        return f"ERROR: GitHub request failed: {e}"
    if resp.status_code == 404:
        return "ERROR: 404 — no such GitHub user."
    if resp.status_code == 403:
        return (
            "BLOCKED: GitHub API rate limit (60/hr unauthenticated). "
            "Retry later; do not loop."
        )
    if resp.status_code != 200:
        return f"ERROR: GitHub API HTTP {resp.status_code}: {resp.text[:200]}"
    try:
        return resp.json()
    except json.JSONDecodeError:
        return "ERROR: GitHub returned non-JSON."


def _build_summary(user: str) -> str | None:
    """The stable text representation of profile health, or an
    ERROR:/BLOCKED: string to pass straight back."""
    profile = _get_json(f"{_API}/users/{user}")
    if isinstance(profile, str):
        return profile
    repos = _get_json(f"{_API}/users/{user}/repos?per_page=100&sort=updated")
    if isinstance(repos, str):
        return repos
    if not isinstance(repos, list):
        return "ERROR: unexpected repos response shape."

    total_stars = sum(int(r.get("stargazers_count") or 0) for r in repos)
    total_forks = sum(int(r.get("forks_count") or 0) for r in repos)
    open_issues = sum(int(r.get("open_issues_count") or 0) for r in repos)

    # Sort by stars then name so the diff is stable when star counts tie.
    ranked = sorted(
        repos,
        key=lambda r: (-int(r.get("stargazers_count") or 0), r.get("name") or ""),
    )

    lines = [
        f"github profile: {user}",
        f"followers: {int(profile.get('followers') or 0)}",
        f"public_repos: {int(profile.get('public_repos') or 0)}",
        f"total_stars: {total_stars}",
        f"total_forks: {total_forks}",
        f"open_issues: {open_issues}",
        "top repos (stars / forks / open issues):",
    ]
    for r in ranked[:_TOP_REPOS]:
        lines.append(
            f"  {r.get('name')}: "
            f"{int(r.get('stargazers_count') or 0)} / "
            f"{int(r.get('forks_count') or 0)} / "
            f"{int(r.get('open_issues_count') or 0)}"
        )
    return "\n".join(lines)


def default_user() -> str:
    """The operator's own GitHub handle — configured or learned, never
    inferred. The weak model guessed it from the user's first name
    ('umang' is a different real account), so the handle is identity that
    must come from a source of truth:

      1. .env HOMUNCULUS_GITHUB_USER (deploy-time config), else
      2. world_state['github_user'] (learned in chat and remembered).

    No hardcoded fallback: an unconfigured deployment returns "" so the
    caller asks the operator rather than watching a stranger."""
    env = os.environ.get("HOMUNCULUS_GITHUB_USER", "").strip()
    if env:
        return env
    try:
        from . import _state
        mem = _state.get_memory()
        if mem is not None:
            return str(mem.world_state.read().get("github_user", "")).strip()
    except Exception:
        pass
    return ""


def github_profile(user: str = "") -> str:
    user = (user or "").strip() or default_user()
    if not user:
        return (
            "ERROR: I don't know the operator's GitHub handle and must not "
            "guess it. ASK the user for their GitHub username, then save it "
            "with update_world_state(github_user='<handle>') so it's "
            "remembered. After saving, call github_profile() again."
        )
    if not _USER_RE.match(user):
        return "ERROR: invalid GitHub username."
    summary = _build_summary(user)
    if summary is None:
        return "ERROR: could not build GitHub profile summary."
    if summary.startswith(("ERROR:", "BLOCKED:")):
        return summary
    return snapshot_or_diff(user, summary)


def snapshot_or_diff(user: str, summary: str) -> str:
    """Diff this week's summary against last week's, then return the
    current summary appended so the model can report absolute numbers
    even on the first run."""
    diff = watch.snapshot_diff(f"gh-profile-{user.lower()}", summary, source=f"{_API}/users/{user}")
    return f"{diff}\n\n--- current snapshot ---\n{summary}"
