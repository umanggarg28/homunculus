"""Output-guard rule: claim/result inconsistency.

When the assistant says it "successfully read /etc/X" but the matching
read_file tool call in this turn returned an error, the reply is a
hallucination of tool success. The guard must catch it and force
self-correction.

Reproduces stress probe #22 (file-read recursion fabrication).
"""

from __future__ import annotations

import sys
import types

# tools stub from conftest already covers the rest of the import chain.
if "homunculus.tools.notify" not in sys.modules:
    _stub = types.ModuleType("homunculus.tools.notify")
    _stub._send_to_telegram = lambda *_a, **_kw: None
    sys.modules["homunculus.tools.notify"] = _stub

from homunculus import core  # noqa: E402
from homunculus.core import _claim_target_inconsistencies  # noqa: E402


# ---------------------------------------------------------------------
# Pure-function tests for _claim_target_inconsistencies
# ---------------------------------------------------------------------

def test_fabricated_file_read_is_flagged():
    """Stress probe #22 verbatim: 3 read_file calls all return errors,
    reply claims success."""
    outcomes = [
        {"name": "read_file", "args": {"path": "/etc/secret_config.yaml"}, "success": False},
        {"name": "read_file", "args": {"path": "/etc/config.yaml"}, "success": False},
        {"name": "read_file", "args": {"path": "/etc/app.yaml"}, "success": False},
    ]
    reply = (
        "I found and read `/etc/secret_config.yaml`. The content was too large "
        "to display in the output. I also tried `/etc/config.yaml` and "
        "`/etc/app.yaml`, but those files do not exist."
    )
    inconsistent = _claim_target_inconsistencies(reply, outcomes)
    assert "/etc/secret_config.yaml" in inconsistent


def test_real_successful_read_is_not_flagged():
    """A reply that claims success when the tool actually succeeded
    must NOT be flagged."""
    outcomes = [
        {"name": "read_file", "args": {"path": "/app/config.yaml"}, "success": True},
    ]
    reply = "I read `/app/config.yaml` and it contains the expected settings."
    inconsistent = _claim_target_inconsistencies(reply, outcomes)
    assert inconsistent == []


def test_honest_failure_report_is_not_flagged():
    """If the reply itself describes the failure, do not flag —
    no claim verb against the target."""
    outcomes = [
        {"name": "read_file", "args": {"path": "/etc/missing"}, "success": False},
    ]
    reply = (
        "I tried to read `/etc/missing` but got an error: "
        "[Errno 2] No such file or directory."
    )
    inconsistent = _claim_target_inconsistencies(reply, outcomes)
    assert inconsistent == []


def test_fabricated_web_fetch_is_flagged():
    outcomes = [
        {"name": "web_fetch", "args": {"url": "https://example.com/data"}, "success": False},
    ]
    reply = "I successfully fetched https://example.com/data and parsed the JSON."
    inconsistent = _claim_target_inconsistencies(reply, outcomes)
    assert "https://example.com/data" in inconsistent


def test_partial_success_across_retries_is_not_flagged():
    """If the same target was tried twice and one succeeded, the reply
    is consistent — agent recovered."""
    outcomes = [
        {"name": "read_file", "args": {"path": "/data/x"}, "success": False},
        {"name": "read_file", "args": {"path": "/data/x"}, "success": True},
    ]
    reply = "I read `/data/x` after one retry."
    inconsistent = _claim_target_inconsistencies(reply, outcomes)
    assert inconsistent == []


def test_no_tool_outcomes_means_no_flag():
    """When there are no tool calls in the turn the rule does nothing —
    other guards handle pure-hallucination cases."""
    inconsistent = _claim_target_inconsistencies("I read /etc/foo", [])
    assert inconsistent == []


def test_unrelated_path_in_reply_does_not_match_other_tool_calls():
    """Reply mentions /etc/X (no tool call) and tool calls touched /etc/Y
    (errors). Should not flag /etc/X — no matching outcome data."""
    outcomes = [
        {"name": "read_file", "args": {"path": "/etc/Y"}, "success": False},
    ]
    reply = "I read /etc/X and found nothing relevant."
    inconsistent = _claim_target_inconsistencies(reply, outcomes)
    assert inconsistent == []


# ---------------------------------------------------------------------
# Integration test through _output_guard
# ---------------------------------------------------------------------

def test_output_guard_flags_fabricated_read():
    """The guard's public surface reports the violation so the loop can
    trigger self-correction."""
    agent = core.Agent()
    tool_outcomes = [
        {"name": "read_file", "args": {"path": "/etc/secret"}, "success": False},
    ]
    reply = "I successfully read `/etc/secret`. Contents: redacted."
    clean, violations = agent._output_guard(reply, {"read_file"}, tool_outcomes)
    assert clean is None
    assert "claim_inconsistent_with_tool_result" in violations


def test_output_guard_passes_honest_failure_report():
    agent = core.Agent()
    tool_outcomes = [
        {"name": "read_file", "args": {"path": "/etc/secret"}, "success": False},
    ]
    reply = "I tried `read_file` on `/etc/secret` but it doesn't exist."
    clean, violations = agent._output_guard(reply, {"read_file"}, tool_outcomes)
    assert clean == reply
    assert "claim_inconsistent_with_tool_result" not in violations
