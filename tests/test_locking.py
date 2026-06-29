"""Cross-process locking regressions.

These are deliberately *not* unit tests with a mocked lock — they spawn real
OS processes that hammer the same file on disk, which is the only way to prove
the `fcntl` advisory lock actually serializes a read-modify-write across the
heartbeat / web / chat processes that share the workspace volume.

Each test asserts the no-lost-update property: with the lock, N concurrent
writers each contributing one record leaves all N records on disk. Without the
lock (the bug this PR fixes), the count would come out short.
"""

from __future__ import annotations

import json
import multiprocessing as mp
from pathlib import Path

import pytest

from homunculus.locking import file_lock

# macOS/py3.12 default to "spawn", so workers must be top-level importable
# functions operating only on picklable args (paths + ints).
_CTX = mp.get_context("spawn")


# ---- the primitive: no lost updates on a shared counter ---------------


def _increment_counter(counter_path_str: str, iterations: int) -> None:
    counter_path = Path(counter_path_str)
    lock_path = counter_path.with_suffix(".lock")
    for _ in range(iterations):
        with file_lock(lock_path):
            current = int(counter_path.read_text()) if counter_path.exists() else 0
            # Read-modify-write with no atomicity of its own: correctness here
            # comes entirely from the surrounding lock.
            counter_path.write_text(str(current + 1))


def test_file_lock_serializes_read_modify_write(tmp_path):
    counter = tmp_path / "counter.txt"
    counter.write_text("0")
    n_procs, iters = 6, 40

    procs = [
        _CTX.Process(target=_increment_counter, args=(str(counter), iters))
        for _ in range(n_procs)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=30)
        assert p.exitcode == 0

    assert int(counter.read_text()) == n_procs * iters


# ---- the real bug: ProposalStore.create under concurrency -------------


def _file_proposal(store_path_str: str, idx: int) -> None:
    # Imported inside the worker so the spawned interpreter resolves it fresh.
    from homunculus.proposals import ProposalStore

    ProposalStore(Path(store_path_str)).create(
        kind="skill_edit",
        skill_name=f"skill_{idx}",
        body=f"body for {idx}",
        rationale="concurrency test",
    )


def test_proposal_store_concurrent_create_loses_no_writes(tmp_path):
    store_path = tmp_path / "proposals.json"
    n = 12

    procs = [
        _CTX.Process(target=_file_proposal, args=(str(store_path), i))
        for i in range(n)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=30)
        assert p.exitcode == 0

    data = json.loads(store_path.read_text())
    assert len(data) == n, f"expected {n} proposals, found {len(data)} — lost write"
    # IDs must be unique: a racing _next_id would hand out duplicates.
    ids = [p["id"] for p in data]
    assert len(set(ids)) == n, f"duplicate proposal ids under concurrency: {ids}"


# ---- agent_controls: concurrent toggles don't drop each other ---------


def _set_control(path_str: str, key: str, value) -> None:
    from homunculus.agent_controls import save_controls

    save_controls({key: value}, path=Path(path_str))


def test_agent_controls_concurrent_updates_all_apply(tmp_path):
    path = tmp_path / "agent_controls.json"
    # Each worker sets a distinct field to a NON-default value; with a lost
    # update one toggle would be silently reverted to its default.
    jobs = [
        ("dry_run", True),     # default False
        ("max_steps", 7),      # default 20
        ("paused", True),      # default False
    ]
    procs = [_CTX.Process(target=_set_control, args=(str(path), k, v)) for k, v in jobs]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=30)
        assert p.exitcode == 0

    final = json.loads(path.read_text())
    for key, value in jobs:
        assert final.get(key) == value, (
            f"{key} expected {value!r}, got {final.get(key)!r} — concurrent update dropped"
        )


@pytest.mark.parametrize("key", ["dry_run", "max_steps", "paused"])
def test_agent_controls_keys_exist(key):
    # Guard the test above against a renamed field silently passing.
    from homunculus.agent_controls import _FIELDS

    assert key in _FIELDS
