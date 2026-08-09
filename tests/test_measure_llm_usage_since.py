"""measure_llm_usage_since attributes per-task LLM spend AND, since the
model-history addition, the model that ran it — the eval harness reads
that field back to split a skill's compliance history across a model
swap instead of blending every model it's ever run under."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from homunculus import llm


def _write_events(path, records):
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def test_measure_llm_usage_since_captures_most_recent_model(tmp_path, monkeypatch):
    log = tmp_path / "_events.jsonl"
    now = datetime.now(UTC)
    _write_events(log, [
        {"ts": (now - timedelta(seconds=20)).isoformat(), "event": "llm_call",
         "model": "openai/gpt-oss-120b", "input_tokens": 10, "output_tokens": 5},
        {"ts": (now - timedelta(seconds=10)).isoformat(), "event": "llm_call",
         "model": "deepseek/deepseek-v4-flash-0731", "input_tokens": 20, "output_tokens": 8},
    ])
    monkeypatch.setenv("HOMUNCULUS_EVENTS_PATH", str(log))

    usage = llm.measure_llm_usage_since(now - timedelta(minutes=1))

    assert usage["model"] == "deepseek/deepseek-v4-flash-0731"
    assert usage["calls"] == 2
    assert usage["input_tokens"] == 30


def test_measure_llm_usage_since_model_empty_when_no_calls_in_window(tmp_path, monkeypatch):
    log = tmp_path / "_events.jsonl"
    monkeypatch.setenv("HOMUNCULUS_EVENTS_PATH", str(log))
    usage = llm.measure_llm_usage_since(datetime.now(UTC))
    assert usage["model"] == ""
    assert usage["calls"] == 0
