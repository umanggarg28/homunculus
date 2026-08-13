"""Tests for the tool-execution permission gate."""

from homunculus.permissions import (
    MUTATING_TOOLS,
    Decision,
    PermissionPolicy,
    PermissionRule,
    clean_tool_name,
    policy_from_mode,
    strip_channel_markup,
)


# --- normalizers -----------------------------------------------------------


def test_strip_channel_markup_repairs_string_args():
    fixed = strip_channel_markup("get_weather", {"city": "Bangalore<|channel|>commentary"})
    assert fixed == {"city": "Bangalore"}


def test_strip_channel_markup_returns_none_when_clean():
    assert strip_channel_markup("get_weather", {"city": "Bangalore"}) is None


def test_strip_channel_markup_leaves_non_strings_alone():
    fixed = strip_channel_markup("rss_feed", {"limit": 5, "url": "http://x<|end|>"})
    assert fixed == {"limit": 5, "url": "http://x"}


def test_clean_tool_name_recovers_intended_tool():
    assert clean_tool_name("news_headlines<|channel|>commentary") == "news_headlines"


def test_clean_tool_name_passes_through_clean_names():
    assert clean_tool_name("news_headlines") == "news_headlines"


def test_clean_tool_name_never_returns_empty():
    # A name that is nothing but markup has no recoverable intent; keeping the
    # original lets the normal unknown-tool error path report it.
    assert clean_tool_name("<|channel|>") == "<|channel|>"


# --- allow / deny ----------------------------------------------------------


def test_default_policy_allows():
    decision = PermissionPolicy().check("read_file", {"path": "notes.md"})
    assert decision.allowed
    assert decision.args == {"path": "notes.md"}


def test_deny_rule_blocks_and_explains():
    policy = PermissionPolicy(
        rules=(PermissionRule("write_file", "deny", "this run is a dry run"),)
    )
    decision = policy.check("write_file", {"path": "x"})
    assert not decision.allowed
    assert "write_file" in decision.message
    assert "dry run" in decision.message


def test_first_matching_rule_wins_over_wildcard():
    policy = PermissionPolicy(
        rules=(
            PermissionRule("read_file", "allow"),
            PermissionRule("*", "deny", "locked down"),
        )
    )
    assert policy.check("read_file", {}).allowed
    assert not policy.check("write_file", {}).allowed


def test_confirm_asks_the_user_when_someone_is_present():
    policy = PermissionPolicy(rules=(PermissionRule("web_post", "confirm"),))
    decision = policy.check("web_post", {})
    assert not decision.allowed
    assert "approval" in decision.message


def test_confirm_collapses_to_deny_when_unattended():
    policy = PermissionPolicy(
        mode="autonomous", rules=(PermissionRule("web_post", "confirm"),)
    )
    decision = policy.check("web_post", {})
    assert not decision.allowed
    assert "unattended" in decision.message


# --- modes -----------------------------------------------------------------


def test_readonly_blocks_every_mutating_tool():
    policy = PermissionPolicy(mode="readonly")
    for tool in MUTATING_TOOLS:
        assert not policy.check(tool, {}).allowed, tool


def test_readonly_still_allows_reads():
    assert PermissionPolicy(mode="readonly").check("read_file", {}).allowed


def test_bypass_ignores_rules():
    policy = PermissionPolicy(
        mode="bypass", rules=(PermissionRule("*", "deny", "nope"),)
    )
    assert policy.check("write_file", {}).allowed


def test_bypass_still_normalizes():
    # Correcting a malformed argument is correctness, not permission — it must
    # survive every mode, including the one that skips the rules.
    decision = PermissionPolicy(mode="bypass").check("get_weather", {"city": "Pune<|x|>"})
    assert decision.allowed
    assert decision.corrected
    assert decision.args == {"city": "Pune"}


def test_with_mode_preserves_rules():
    policy = PermissionPolicy(rules=(PermissionRule("write_file", "deny"),))
    switched = policy.with_mode("autonomous")
    assert switched.mode == "autonomous"
    assert switched.rules == policy.rules


# --- normalization interacts correctly with denial -------------------------


def test_denied_call_still_reports_corrected_args():
    # The recorded arguments should describe what the model meant, so a later
    # reader of the trace sees the real intent rather than the mistyped form.
    policy = PermissionPolicy(rules=(PermissionRule("write_file", "deny"),))
    decision = policy.check("write_file", {"path": "notes.md<|channel|>"})
    assert not decision.allowed
    assert decision.corrected
    assert decision.args == {"path": "notes.md"}


def test_normalizer_failure_does_not_block_the_call():
    def explode(name, args):
        raise RuntimeError("boom")

    policy = PermissionPolicy(normalizers=(explode,))
    decision = policy.check("read_file", {"path": "x"})
    assert decision.allowed
    assert decision.args == {"path": "x"}
    assert not decision.corrected


def test_check_does_not_mutate_the_caller_dict():
    args = {"city": "Pune<|x|>"}
    PermissionPolicy().check("get_weather", args)
    assert args == {"city": "Pune<|x|>"}


# --- config plumbing -------------------------------------------------------


def test_policy_from_mode_reads_known_modes():
    assert policy_from_mode("readonly").mode == "readonly"


def test_policy_from_mode_falls_back_on_nonsense():
    # A typo in a deployment's env should not stop the agent from starting.
    assert policy_from_mode("redonly").mode == "default"
    assert policy_from_mode(None).mode == "default"


def test_decision_is_hashable_and_frozen():
    d = Decision(True, {}, "")
    assert d.allowed
    assert d.message == ""
