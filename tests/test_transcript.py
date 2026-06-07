"""Transcript class — append-only message store with stable IDs.

Pure unit tests, no Agent integration. PR #110 will wire Agent.history
to read from Transcript; this PR just lands the storage layer.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from transcript import Transcript


# ---- basic round-trip --------------------------------------------------


def test_append_returns_zero_padded_sequential_ids(tmp_path: Path) -> None:
    t = Transcript(tmp_path / "tx.jsonl")
    a = t.append({"role": "user", "content": "first"})
    b = t.append({"role": "assistant", "content": "second"})
    c = t.append({"role": "user", "content": "third"})
    assert a == "000001"
    assert b == "000002"
    assert c == "000003"


def test_get_returns_message_dict(tmp_path: Path) -> None:
    t = Transcript(tmp_path / "tx.jsonl")
    rid = t.append({"role": "user", "content": "hello"})
    msg = t.get(rid)
    assert msg == {"role": "user", "content": "hello"}


def test_get_unknown_id_returns_none(tmp_path: Path) -> None:
    t = Transcript(tmp_path / "tx.jsonl")
    t.append({"role": "user", "content": "hi"})
    assert t.get("999999") is None


def test_get_many_preserves_caller_order(tmp_path: Path) -> None:
    """get_many must respect the requested order — the pointer list IS
    the order the LLM sees, so reordering would silently corrupt context."""
    t = Transcript(tmp_path / "tx.jsonl")
    a = t.append({"role": "user", "content": "1"})
    b = t.append({"role": "assistant", "content": "2"})
    c = t.append({"role": "user", "content": "3"})
    out = t.get_many([c, a, b])
    assert out == [
        {"role": "user", "content": "3"},
        {"role": "user", "content": "1"},
        {"role": "assistant", "content": "2"},
    ]


def test_get_many_silently_drops_missing_ids(tmp_path: Path) -> None:
    """Missing IDs degrade to shorter history rather than crashing."""
    t = Transcript(tmp_path / "tx.jsonl")
    a = t.append({"role": "user", "content": "kept"})
    out = t.get_many([a, "999999"])
    assert out == [{"role": "user", "content": "kept"}]


def test_get_many_empty_list(tmp_path: Path) -> None:
    t = Transcript(tmp_path / "tx.jsonl")
    assert t.get_many([]) == []


# ---- persistence + restart --------------------------------------------


def test_seq_resumes_after_restart(tmp_path: Path) -> None:
    """A new Transcript pointing at an existing file must continue
    numbering from max+1, not restart at 1 (would collide IDs)."""
    path = tmp_path / "tx.jsonl"
    t1 = Transcript(path)
    t1.append({"role": "user", "content": "a"})
    t1.append({"role": "user", "content": "b"})

    t2 = Transcript(path)
    new_id = t2.append({"role": "user", "content": "c"})
    assert new_id == "000003"


def test_all_returns_id_msg_tuples_in_order(tmp_path: Path) -> None:
    t = Transcript(tmp_path / "tx.jsonl")
    t.append({"role": "user", "content": "a"})
    t.append({"role": "user", "content": "b"})
    out = t.all()
    assert out == [
        ("000001", {"role": "user", "content": "a"}),
        ("000002", {"role": "user", "content": "b"}),
    ]


def test_all_ids_in_order(tmp_path: Path) -> None:
    t = Transcript(tmp_path / "tx.jsonl")
    for _ in range(5):
        t.append({"role": "user", "content": "x"})
    assert t.all_ids() == ["000001", "000002", "000003", "000004", "000005"]


def test_len_counts_records(tmp_path: Path) -> None:
    t = Transcript(tmp_path / "tx.jsonl")
    assert len(t) == 0
    t.append({"role": "user", "content": "a"})
    t.append({"role": "user", "content": "b"})
    assert len(t) == 2


# ---- robustness --------------------------------------------------------


def test_malformed_line_is_skipped(tmp_path: Path) -> None:
    """A torn write at shutdown would leave a fragment. Reading must
    skip it rather than crash, mirroring load_chat_log's behavior."""
    path = tmp_path / "tx.jsonl"
    t = Transcript(path)
    t.append({"role": "user", "content": "valid"})
    with path.open("a", encoding="utf-8") as f:
        f.write("not-json-at-all\n")
        f.write('{"id": "000002", "msg": "not a dict"}\n')  # wrong type
    out = t.all()
    assert out == [("000001", {"role": "user", "content": "valid"})]


def test_malformed_line_does_not_corrupt_next_seq(tmp_path: Path) -> None:
    """After a torn write the next legitimate append must still get an
    ID greater than every well-formed prior ID."""
    path = tmp_path / "tx.jsonl"
    Transcript(path).append({"role": "user", "content": "first"})
    with path.open("a", encoding="utf-8") as f:
        f.write("garbage\n")
    t = Transcript(path)
    new_id = t.append({"role": "user", "content": "second"})
    assert new_id == "000002"


def test_append_rejects_non_dict(tmp_path: Path) -> None:
    t = Transcript(tmp_path / "tx.jsonl")
    with pytest.raises(TypeError):
        t.append("not a dict")  # type: ignore[arg-type]


def test_append_many_preserves_order_and_returns_ids(tmp_path: Path) -> None:
    t = Transcript(tmp_path / "tx.jsonl")
    msgs = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
    ]
    ids = t.append_many(msgs)
    assert ids == ["000001", "000002", "000003"]
    assert t.get_many(ids) == msgs


def test_append_many_empty_is_noop(tmp_path: Path) -> None:
    t = Transcript(tmp_path / "tx.jsonl")
    assert t.append_many([]) == []
    assert len(t) == 0


def test_unicode_round_trip(tmp_path: Path) -> None:
    """ensure_ascii=False so emoji/CJK content survives without escape bloat."""
    t = Transcript(tmp_path / "tx.jsonl")
    payload = {"role": "user", "content": "café 你好 🌶️"}
    rid = t.append(payload)
    assert t.get(rid) == payload


def test_clear_removes_file_and_resets_seq(tmp_path: Path) -> None:
    path = tmp_path / "tx.jsonl"
    t = Transcript(path)
    t.append({"role": "user", "content": "a"})
    t.append({"role": "user", "content": "b"})
    t.clear()
    assert not path.exists()
    assert t.append({"role": "user", "content": "fresh"}) == "000001"


def test_empty_transcript_reads_cleanly(tmp_path: Path) -> None:
    t = Transcript(tmp_path / "doesnt-exist.jsonl")
    assert t.all() == []
    assert t.all_ids() == []
    assert t.get_many(["000001"]) == []
    assert len(t) == 0


# ---- concurrency -------------------------------------------------------


def test_concurrent_appends_get_unique_ids(tmp_path: Path) -> None:
    """flock must serialize writers so two threads don't both grab
    the same sequence number."""
    path = tmp_path / "tx.jsonl"
    # Each writer gets its own Transcript instance — simulates two
    # processes (transports) sharing the same file.
    writers = [Transcript(path) for _ in range(4)]
    results: list[str] = []
    results_lock = threading.Lock()
    barrier = threading.Barrier(len(writers))

    def write(t: Transcript, n: int) -> None:
        barrier.wait()
        for i in range(n):
            rid = t.append({"role": "user", "content": f"{id(t)}-{i}"})
            with results_lock:
                results.append(rid)

    threads = [threading.Thread(target=write, args=(w, 10)) for w in writers]
    for thr in threads:
        thr.start()
    for thr in threads:
        thr.join()

    assert len(results) == 40
    assert len(set(results)) == 40, "IDs must be unique under concurrent appends"
