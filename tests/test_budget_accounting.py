"""The budget ceiling relies on parsing _events.jsonl for today's spend. If
that log is unreadable or corrupt, accounting fails *open* (spend reads as 0 =
"full budget"). That's the right resilience choice — but it must not be silent,
or the one hard cost guarantee evaporates with no breadcrumb. These tests pin
the degraded-accounting alarm.
"""

from __future__ import annotations

import pytest

from homunculus import llm


@pytest.fixture(autouse=True)
def _reset_warn_flag():
    # The alarm fires at most once per process; reset around each test.
    llm._budget_degraded_warned = False
    yield
    llm._budget_degraded_warned = False


def _capture_events(monkeypatch):
    emitted: list[tuple[str, dict]] = []
    monkeypatch.setattr(llm.events, "emit", lambda name, **kw: emitted.append((name, kw)))
    return emitted


def test_corrupt_log_emits_degraded_event(tmp_path, monkeypatch):
    log = tmp_path / "_events.jsonl"
    # Non-empty, but not a single valid JSON line → corruption, not a quiet day.
    log.write_text("}{ this is not json\n\x00\x00 garbage\n", encoding="utf-8")
    monkeypatch.setenv("HOMUNCULUS_EVENTS_PATH", str(log))
    emitted = _capture_events(monkeypatch)

    spend = llm._today_spend_cents()

    assert spend == 0.0  # fails open — agent does not brick
    assert any(name == "budget_accounting_degraded" for name, _ in emitted), (
        "corrupt events log must raise the degraded-accounting alarm"
    )


def test_missing_log_is_not_degraded(tmp_path, monkeypatch):
    # A genuinely-absent log (fresh deploy) is normal, not corruption.
    monkeypatch.setenv("HOMUNCULUS_EVENTS_PATH", str(tmp_path / "nope.jsonl"))
    emitted = _capture_events(monkeypatch)

    assert llm._today_spend_cents() == 0.0
    assert not emitted, "a missing log must NOT raise the degraded alarm"


def test_valid_log_with_no_spend_is_not_degraded(tmp_path, monkeypatch):
    # Early in the day there are real events but no paid llm_call yet — normal.
    log = tmp_path / "_events.jsonl"
    log.write_text('{"event": "heartbeat", "ts": "2999-01-01T00:00:00"}\n', encoding="utf-8")
    monkeypatch.setenv("HOMUNCULUS_EVENTS_PATH", str(log))
    emitted = _capture_events(monkeypatch)

    llm._today_spend_cents()
    assert not any(name == "budget_accounting_degraded" for name, _ in emitted), (
        "a parseable log with no spend is a quiet day, not degraded accounting"
    )


def test_alarm_fires_at_most_once(tmp_path, monkeypatch):
    log = tmp_path / "_events.jsonl"
    log.write_text("not json at all\n", encoding="utf-8")
    monkeypatch.setenv("HOMUNCULUS_EVENTS_PATH", str(log))
    emitted = _capture_events(monkeypatch)

    llm._today_spend_cents()
    llm._today_spend_cents()
    llm._today_spend_cents()

    count = sum(1 for name, _ in emitted if name == "budget_accounting_degraded")
    assert count == 1, f"degraded alarm should fire once per process, fired {count}×"
