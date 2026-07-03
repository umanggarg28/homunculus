"""Incremental event-log reads — the dashboard must not rescan history.

Two mechanisms, same accounting pattern as llm.py's budget scan:
- events.read_appended_records: byte-offset reader consuming only complete
  appended lines (feeds the /api/skills per-tool counter accumulator).
- stats._tail_lines_covering: bounded backward read that stops once the
  buffer covers the requested window (feeds summarize_events).
"""

import json
from datetime import datetime, timedelta, UTC

from homunculus import events
from homunculus import stats
# web_api must load before the router modules it initializes (they reach
# back into it as `wa.*`; importing a router first is a circular import).
from homunculus.transports import web_api  # noqa: F401
from homunculus.transports.web import skills as sk


def _write_lines(path, records):
    with path.open("a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


# ---------------------------------------------------- read_appended_records


def test_appended_records_are_picked_up_from_offset(tmp_path):
    p = tmp_path / "ev.jsonl"
    _write_lines(p, [{"event": "a"}, {"event": "b"}])
    recs, off = events.read_appended_records(p, 0)
    assert [r["event"] for r in recs] == ["a", "b"]

    _write_lines(p, [{"event": "c"}])
    recs2, off2 = events.read_appended_records(p, off)
    assert [r["event"] for r in recs2] == ["c"]
    assert off2 > off


def test_partial_trailing_line_is_never_half_parsed(tmp_path):
    p = tmp_path / "ev.jsonl"
    _write_lines(p, [{"event": "a"}])
    with p.open("a", encoding="utf-8") as f:
        f.write('{"event": "in-flig')  # another process mid-append

    recs, off = events.read_appended_records(p, 0)
    assert [r["event"] for r in recs] == ["a"]

    with p.open("a", encoding="utf-8") as f:
        f.write('ht"}\n')
    recs2, _ = events.read_appended_records(p, off)
    assert [r["event"] for r in recs2] == ["in-flight"]


# ------------------------------------------------------- skills accumulator


def _ev(event, name, ts, **extra):
    return {"ts": ts, "event": event, "name": name, **extra}


def test_skills_counters_accumulate_without_rescan(tmp_path, monkeypatch):
    p = tmp_path / "ev.jsonl"
    now = datetime.now(UTC).isoformat(timespec="seconds")
    _write_lines(p, [
        _ev("tool_call", "notify", now),
        _ev("tool_result", "notify", now, result="DELIVERED"),
    ])
    monkeypatch.setattr(sk.wa, "EVENTS_PATH", p)
    monkeypatch.setattr(sk, "_scan_state", {"offset": 0, "counters": {}})

    c1 = sk._advance_scan()
    assert c1["notify"]["call_count"] == 1
    assert c1["notify"]["success_count"] == 1

    _write_lines(p, [
        _ev("tool_call", "notify", now),
        _ev("tool_result", "notify", now, result="ERROR: blocked"),
    ])
    c2 = sk._advance_scan()
    assert c2["notify"]["call_count"] == 2
    assert c2["notify"]["failure_count"] == 1
    assert c2["notify"]["last_status"] == "failure"


def test_skills_rotation_resets_the_accumulator(tmp_path, monkeypatch):
    p = tmp_path / "ev.jsonl"
    now = datetime.now(UTC).isoformat(timespec="seconds")
    _write_lines(p, [_ev("tool_call", "notify", now)] * 5)
    monkeypatch.setattr(sk.wa, "EVENTS_PATH", p)
    monkeypatch.setattr(sk, "_scan_state", {"offset": 0, "counters": {}})
    assert sk._advance_scan()["notify"]["call_count"] == 5

    # Rotation: rewrite the file smaller than the consumed offset.
    p.write_text("", encoding="utf-8")
    _write_lines(p, [_ev("tool_call", "notify", now)])
    assert sk._advance_scan()["notify"]["call_count"] == 1


def test_recent_calls_capped(tmp_path, monkeypatch):
    p = tmp_path / "ev.jsonl"
    now = datetime.now(UTC).isoformat(timespec="seconds")
    _write_lines(p, [_ev("tool_call", "notify", now)] * (sk._RECENT_CALLS_CAP + 20))
    monkeypatch.setattr(sk.wa, "EVENTS_PATH", p)
    monkeypatch.setattr(sk, "_scan_state", {"offset": 0, "counters": {}})
    c = sk._advance_scan()
    assert len(c["notify"]["recent_calls"]) == sk._RECENT_CALLS_CAP


# ----------------------------------------------------------- stats tail read


def test_tail_read_covers_the_window_across_blocks(tmp_path):
    """Force multiple backward blocks with a tiny chunk size and check the
    returned tail starts at (or before) the window boundary."""
    p = tmp_path / "ev.jsonl"
    base = datetime(2026, 7, 1, tzinfo=UTC)
    records = [
        {"ts": (base + timedelta(minutes=i)).isoformat(), "event": "tool_call",
         "name": "notify", "pad": "x" * 40}
        for i in range(200)
    ]
    _write_lines(p, records)
    since = base + timedelta(minutes=150)

    lines = stats._tail_lines_covering(p, since, chunk=256)
    parsed = [json.loads(ln) for ln in lines if ln.strip()]
    # Everything inside the window must be present…
    in_window = [r for r in parsed
                 if datetime.fromisoformat(r["ts"]) >= since]
    assert len(in_window) == 50
    # …and the read stopped early rather than returning the whole file.
    assert len(parsed) < 200


def test_summarize_events_matches_full_scan_semantics(tmp_path):
    """End-to-end: the bounded read produces the same summary the whole-file
    readlines() implementation did."""
    p = tmp_path / "ev.jsonl"
    base = datetime.now(UTC) - timedelta(hours=3)
    _write_lines(p, [
        {"ts": (base + timedelta(minutes=i)).isoformat(), "event": "tool_call",
         "name": "complete_task" if i % 2 else "notify"}
        for i in range(10)
    ])
    since = base + timedelta(minutes=4, seconds=30)
    s = stats.summarize_events(since, path=p)
    assert s["events"] == 5  # minutes 5..9
    assert s["tasks_fired"] == 3  # odd minutes 5, 7, 9
