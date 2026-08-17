"""The audit log must survive concurrent services.

rotate() truncated the file before rewriting it, unlocked, while five services
appended to it and two of them called rotate at startup. A reader that landed
in the truncation window saw a partial log; if that reader rotated and wrote
its view back, the difference was gone. Nine days of history disappeared that
way, and it was only noticed because a task's failure could not be explained
afterwards.

locking.py exists for exactly this and every other store already used it.
"""

from __future__ import annotations

import importlib
import json
import os
from concurrent.futures import ThreadPoolExecutor


def _events_module(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMUNCULUS_EVENTS_PATH", str(tmp_path / "_events.jsonl"))
    import homunculus.events as ev
    return importlib.reload(ev)


def _write_days(path, days):
    from datetime import UTC, datetime, timedelta
    now = datetime.now(UTC)
    with open(path, "w", encoding="utf-8") as f:
        for d in days:
            ts = (now - timedelta(days=d)).isoformat(timespec="seconds")
            f.write(json.dumps({"ts": ts, "event": f"day{d}"}) + "\n")


def test_rotation_keeps_everything_inside_the_window(tmp_path, monkeypatch):
    ev = _events_module(tmp_path, monkeypatch)
    _write_days(ev._EVENTS_PATH, [30, 20, 10, 5, 1])
    ev.rotate(keep_days=14)
    kept = [json.loads(l)["event"] for l in open(ev._EVENTS_PATH, encoding="utf-8") if l.strip()]
    assert kept == ["day10", "day5", "day1"], "a middle block must never vanish"


def test_a_reader_never_sees_a_half_written_log(tmp_path, monkeypatch):
    """The replace is atomic, so every read returns a complete file."""
    ev = _events_module(tmp_path, monkeypatch)
    _write_days(ev._EVENTS_PATH, [30, 20, 10, 5, 1])

    seen: list[int] = []

    def reader():
        for _ in range(60):
            text = ev._EVENTS_PATH.read_text(encoding="utf-8", errors="replace")
            lines = [l for l in text.splitlines() if l.strip()]
            seen.append(len(lines))

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(reader)
        f2 = pool.submit(ev.rotate, 14)
        f1.result()
        f2.result()

    assert 0 not in seen, "a reader saw an empty log mid-rotation"
    assert set(seen) <= {5, 3}, f"a reader saw a partial log: {sorted(set(seen))}"


def test_concurrent_appends_are_not_lost_to_a_rotation(tmp_path, monkeypatch):
    ev = _events_module(tmp_path, monkeypatch)
    _write_days(ev._EVENTS_PATH, [30, 1])

    def appender():
        for i in range(40):
            ev.emit("live", n=i)

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(appender)
        f2 = pool.submit(ev.rotate, 14)
        f1.result()
        f2.result()

    events = [json.loads(l) for l in open(ev._EVENTS_PATH, encoding="utf-8") if l.strip()]
    assert sum(1 for e in events if e.get("event") == "live") == 40


def test_two_rotations_racing_do_not_destroy_data(tmp_path, monkeypatch):
    """Heartbeat and web both rotate at startup, and `docker compose up` starts
    them together."""
    ev = _events_module(tmp_path, monkeypatch)
    _write_days(ev._EVENTS_PATH, [30, 20, 10, 5, 1])

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(ev.rotate, 14)
        b = pool.submit(ev.rotate, 14)
        a.result()
        b.result()

    kept = [json.loads(l)["event"] for l in open(ev._EVENTS_PATH, encoding="utf-8") if l.strip()]
    assert kept == ["day10", "day5", "day1"]


def test_rotation_leaves_no_temp_file_behind(tmp_path, monkeypatch):
    ev = _events_module(tmp_path, monkeypatch)
    _write_days(ev._EVENTS_PATH, [30, 1])
    ev.rotate(keep_days=14)
    assert not [p for p in os.listdir(tmp_path) if p.endswith(".tmp")]
