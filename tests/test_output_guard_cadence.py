"""Output-guard rule: unsupported-cadence over-claims.

Stress baseline probe #9: "Remind me every weekday at 7am to journal, but skip
public holidays." The recurrence vocabulary is only {none, daily, weekly}
(tasks.ALLOWED_RECURRENCE), so weekday-only / skip-holidays cannot be expressed —
yet the agent claimed it did. The guard flags such a claim when a scheduling
tool ran, unless the reply honestly owns the limitation.
"""

from __future__ import annotations

import sys
import types

if "homunculus.tools.notify" not in sys.modules:
    _stub = types.ModuleType("homunculus.tools.notify")
    _stub._send_to_telegram = lambda *_a, **_kw: None
    sys.modules["homunculus.tools.notify"] = _stub

from homunculus import core  # noqa: E402
from homunculus.output_guard import unsupported_cadence_claim  # noqa: E402


def test_weekday_skip_holiday_claim_is_flagged():
    agent = core.Agent()
    reply = "Your weekday journal reminder is set for 7am, Monday through Friday, skipping public holidays."
    clean, violations = agent._output_guard(reply, {"create_task"}, [])
    assert clean is None
    assert "unsupported_cadence_claim" in violations


def test_honest_limitation_is_not_flagged():
    # Same cadence words, but the reply owns the limit → honest, not a claim.
    agent = core.Agent()
    reply = (
        "I've set a daily 7am reminder. I can't auto-skip public holidays, "
        "so you'll need to pause it on those days."
    )
    clean, violations = agent._output_guard(reply, {"create_task"}, [])
    assert "unsupported_cadence_claim" not in violations


def test_plain_daily_reminder_is_clean():
    assert unsupported_cadence_claim("Daily 7am reminder set.", {"create_task"}) is False


def test_no_scheduling_tool_means_no_check():
    # "weekday" in a non-scheduling context must not trip the rule.
    reply = "Most weekday mornings are busy for standups."
    assert unsupported_cadence_claim(reply, {"web_search"}) is False
