"""NotificationQueue — extracted from Memory god class (Bundle 2 #2).

The queue bridges notify() (heartbeat-side) to user-facing chat
context (telegram/web). Drain is atomic across concurrent drainers so
two transports don't double-inject the same notifications.
"""

from __future__ import annotations

import threading
from pathlib import Path

from homunculus.notifications import NotificationQueue


def test_queue_then_drain_returns_entry(tmp_path: Path) -> None:
    q = NotificationQueue(tmp_path)
    q.queue("hello world")
    fresh = q.drain()
    assert len(fresh) == 1
    assert fresh[0]["text"] == "hello world"
    assert isinstance(fresh[0]["ts"], float)


def test_drain_is_idempotent(tmp_path: Path) -> None:
    """A second drain returns nothing — the pointer advanced."""
    q = NotificationQueue(tmp_path)
    q.queue("a")
    q.queue("b")
    assert len(q.drain()) == 2
    assert q.drain() == []


def test_queue_after_drain_returns_only_new(tmp_path: Path) -> None:
    q = NotificationQueue(tmp_path)
    q.queue("old")
    q.drain()
    q.queue("new")
    fresh = q.drain()
    assert [e["text"] for e in fresh] == ["new"]


def test_empty_log_returns_empty_list(tmp_path: Path) -> None:
    q = NotificationQueue(tmp_path)
    assert q.drain() == []


def test_malformed_lines_are_skipped(tmp_path: Path) -> None:
    q = NotificationQueue(tmp_path)
    q.queue("good")
    with q.log_path.open("a", encoding="utf-8") as f:
        f.write("not-json\n")
        f.write('["wrong-shape"]\n')  # not a dict
        f.write('{"ts": "not-a-number", "text": "x"}\n')
    fresh = q.drain()
    assert [e["text"] for e in fresh] == ["good"]


def test_concurrent_drains_split_entries_no_duplicates(tmp_path: Path) -> None:
    """Two drainers (telegram + web) racing on the same queue must not
    both see the same notification. The flock serializes pointer
    updates, so each entry is delivered exactly once across all drainers."""
    q_writer = NotificationQueue(tmp_path)
    for i in range(20):
        q_writer.queue(f"msg-{i}")

    results: list[list[dict]] = []
    results_lock = threading.Lock()
    barrier = threading.Barrier(2)

    def drain_once() -> None:
        # Each thread holds its own NotificationQueue handle — simulates
        # two transports running as separate processes.
        q = NotificationQueue(tmp_path)
        barrier.wait()
        out = q.drain()
        with results_lock:
            results.append(out)

    threads = [threading.Thread(target=drain_once) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Each entry delivered exactly once across the two drainers.
    all_texts = [e["text"] for batch in results for e in batch]
    assert len(all_texts) == 20  # no losses
    assert len(set(all_texts)) == 20  # no duplicates


def test_queue_appends_to_existing_file(tmp_path: Path) -> None:
    q = NotificationQueue(tmp_path)
    q.queue("first")
    q.queue("second")
    text = q.log_path.read_text(encoding="utf-8")
    assert text.count("\n") == 2


def test_pointer_persists_across_instances(tmp_path: Path) -> None:
    """A fresh NotificationQueue object pointing at the same dir must
    respect the consumed pointer left by a prior instance."""
    a = NotificationQueue(tmp_path)
    a.queue("seen")
    a.drain()  # advances pointer

    b = NotificationQueue(tmp_path)
    assert b.drain() == []  # new instance respects existing pointer


def test_memory_notifications_property_returns_queue(tmp_path: Path) -> None:
    """Memory.notifications must return a NotificationQueue rooted at
    the same dir, so legacy `mem.notifications.queue(...)` callers
    work unchanged after the extraction."""
    from homunculus.memory import Memory
    mem = Memory(tmp_path)
    q = mem.notifications
    assert isinstance(q, NotificationQueue)
    assert q.root == tmp_path
    # Same property returns the same instance (lazy singleton)
    assert mem.notifications is q
