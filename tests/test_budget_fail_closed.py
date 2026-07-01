"""The budget must fail CLOSED on cost.

Two properties pinned here:

1. A paid model missing from the pricing table is costed at conservative
   default rates — counted in spend and blockable — instead of being
   invisible to the accounting (the old `_is_known_paid_model` returned
   False for unknown models, so `/use some/new-paid-model` was neither
   costed nor capped).

2. Spend accounting is incremental: after one full scan of the events log,
   later checks parse only the appended bytes. Appends must be picked up,
   and a rotation (file shrinks) must trigger a clean rescan.
"""

from __future__ import annotations

import json
from datetime import datetime, UTC

import pytest

from homunculus import llm


@pytest.fixture(autouse=True)
def _reset_module_state():
    llm._spend_cache = None
    llm._budget_disabled_warned = False
    llm._budget_degraded_warned = False
    yield
    llm._spend_cache = None
    llm._budget_disabled_warned = False
    llm._budget_degraded_warned = False


def _llm_call_line(model: str, input_tokens: int = 1_000_000) -> str:
    return json.dumps({
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "event": "llm_call",
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": 0,
        "cached_tokens": 0,
    }) + "\n"


# ---- pricing fail-closed --------------------------------------------------

def test_unknown_paid_model_gets_default_pricing():
    assert llm._pricing_for("someone/new-frontier-model") == llm._DEFAULT_PAID_PRICING_CENTS


def test_known_model_keeps_table_pricing():
    assert llm._pricing_for("gemini-2.5-flash") == llm._MODEL_PRICING_CENTS["gemini-2.5-flash"]


def test_free_routes_cost_nothing():
    assert llm._pricing_for("openai/gpt-oss-120b:free") is None
    assert llm._pricing_for("") is None


def test_unknown_paid_model_is_counted_and_blocked(tmp_path, monkeypatch):
    """One 1M-input call to an UNLISTED paid model must exceed a 1¢ budget."""
    from homunculus.config import HomunculusConfig, ProviderConfig, set_config
    events = tmp_path / "_events.jsonl"
    events.write_text(_llm_call_line("someone/new-frontier-model"), encoding="utf-8")
    monkeypatch.setenv("HOMUNCULUS_EVENTS_PATH", str(events))
    monkeypatch.setenv("HOMUNCULUS_DAILY_BUDGET_USD", "0.01")
    set_config(HomunculusConfig(provider=ProviderConfig(enforce_daily_budget=True)))
    try:
        assert llm._today_spend_cents() > 1.0
        assert llm._budget_blocks_model("someone/new-frontier-model") is True
    finally:
        set_config(None)


def test_no_budget_configured_warns_once_for_paid_models(monkeypatch, caplog):
    """Enforcement on + no budget = no ceiling. That must be loud, once."""
    from homunculus.config import HomunculusConfig, ProviderConfig, set_config
    monkeypatch.delenv("HOMUNCULUS_DAILY_BUDGET_USD", raising=False)
    set_config(HomunculusConfig(provider=ProviderConfig(enforce_daily_budget=True)))
    try:
        with caplog.at_level("WARNING", logger="homunculus.llm"):
            assert llm._budget_blocks_model("gemini-2.5-flash") is False
            assert llm._budget_blocks_model("gemini-2.5-flash") is False
        warnings = [r for r in caplog.records if "NO cost ceiling" in r.getMessage()]
        assert len(warnings) == 1
    finally:
        set_config(None)


# ---- incremental spend cache ------------------------------------------------

def test_spend_picks_up_appended_calls(tmp_path, monkeypatch):
    events = tmp_path / "_events.jsonl"
    events.write_text(_llm_call_line("gemini-2.5-flash"), encoding="utf-8")
    monkeypatch.setenv("HOMUNCULUS_EVENTS_PATH", str(events))

    first = llm._today_spend_cents()
    assert first > 0

    with events.open("a", encoding="utf-8") as f:
        f.write(_llm_call_line("gemini-2.5-flash"))

    second = llm._today_spend_cents()
    assert second == pytest.approx(first * 2)


def test_spend_survives_rotation(tmp_path, monkeypatch):
    """A shrunken file (events.rotate) must trigger a full rescan, not a
    stale-offset read past EOF."""
    events = tmp_path / "_events.jsonl"
    events.write_text(_llm_call_line("gemini-2.5-flash") * 3, encoding="utf-8")
    monkeypatch.setenv("HOMUNCULUS_EVENTS_PATH", str(events))

    triple = llm._today_spend_cents()
    assert triple > 0

    events.write_text(_llm_call_line("gemini-2.5-flash"), encoding="utf-8")
    single = llm._today_spend_cents()
    assert single == pytest.approx(triple / 3)


def test_partial_trailing_line_is_not_lost(tmp_path, monkeypatch):
    """A line another process is mid-append must not be half-counted and
    skipped: the offset stops at the last newline, so the record is read
    complete on the next check."""
    events = tmp_path / "_events.jsonl"
    full_line = _llm_call_line("gemini-2.5-flash")
    events.write_text(full_line, encoding="utf-8")
    monkeypatch.setenv("HOMUNCULUS_EVENTS_PATH", str(events))

    first = llm._today_spend_cents()

    # Simulate an in-flight append: half a record, no newline yet.
    half = _llm_call_line("gemini-2.5-flash").rstrip("\n")
    cut = len(half) // 2
    with events.open("a", encoding="utf-8") as f:
        f.write(half[:cut])
    assert llm._today_spend_cents() == pytest.approx(first)

    # The writer finishes the line.
    with events.open("a", encoding="utf-8") as f:
        f.write(half[cut:] + "\n")
    assert llm._today_spend_cents() == pytest.approx(first * 2)
