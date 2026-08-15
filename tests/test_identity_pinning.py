"""A scheduled run must not be steered onto a stranger's identity.

A weekly github-health run called github_profile(user="system") — a real
GitHub account — and the delivery reported that stranger's 72 followers and
2 stars as the operator's. Every guard passed: the numbers were genuinely
fetched from the API, the criteria found "Totals:", the length was fine.
Only the identity was invented, and nothing downstream inspects a tool's
arguments.

The tool deliberately allows looking up other people, which is legitimate in
chat and never legitimate unattended: a scheduled run has nobody who could
have asked. So this is pinned per-context in the permission gate, not removed
from the tool.
"""

from __future__ import annotations

from homunculus.permissions import PermissionPolicy, pin_operator_identity


def _policy(operator="umanggarg28"):
    return PermissionPolicy(normalizers=(pin_operator_identity(operator),))


def test_a_guessed_handle_is_corrected_to_the_operator():
    decision = _policy().check("github_profile", {"user": "system"})
    assert decision.allowed
    assert decision.corrected, "a repaired call must be reported as corrected"
    assert decision.args == {"user": "umanggarg28"}


def test_the_operators_own_handle_is_left_alone():
    decision = _policy().check("github_profile", {"user": "umanggarg28"})
    assert not decision.corrected


def test_case_differences_are_not_a_different_person():
    assert not _policy().check("github_profile", {"user": "UmangGarg28"}).corrected


def test_an_absent_handle_is_left_for_the_tool_to_default():
    """github_profile() with no argument already resolves the operator itself;
    injecting one here would only duplicate that."""
    assert not _policy().check("github_profile", {}).corrected


def test_other_tools_are_untouched():
    decision = _policy().check("web_search", {"user": "system"})
    assert not decision.corrected


def test_an_unconfigured_deployment_changes_nothing():
    """With no configured handle there is nothing authoritative to pin to, and
    inventing one here would be the very bug this prevents."""
    assert not _policy("").check("github_profile", {"user": "system"}).corrected


def test_the_task_policy_pins_identity_and_keeps_the_defaults():
    from homunculus.heartbeat import build_task_permissions
    from homunculus.permissions import strip_channel_markup

    policy = build_task_permissions()
    assert strip_channel_markup in policy.normalizers, "must keep the default repairs"
    assert len(policy.normalizers) > 1
