"""Canonical cross-process file lock.

A handful of stores keep their state as plain files on the shared workspace
volume and mutate them with a read-modify-write sequence. Because the heartbeat,
web, and chat transports run as separate processes against the same volume, an
unguarded RMW can lose writes (two processes load the same JSON, each mutates a
different field, the second write clobbers the first).

The fix is an advisory ``flock`` on a sidecar ``.lock`` file held for the whole
critical section. This module is the single home for that primitive — it was
previously copy-pasted into ``memory``, ``stores``, ``transcript``,
``notifications`` and ``skills``. Those all delegate here now.

Design notes:
  - Non-blocking acquire in a bounded retry loop (50 × 0.1s ≈ 5s) so a wedged
    holder surfaces as a ``RuntimeError`` instead of hanging a service forever.
  - The lock is on a *sidecar* file, never the data file, so truncation/rename
    of the data file (atomic temp-write + replace) doesn't disturb the lock.
  - Advisory only: it coordinates *our* processes, which all route writes
    through these helpers. It is not a guard against unrelated tools.
"""

from __future__ import annotations

import errno
import fcntl
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

#: Retry budget: 50 attempts × 0.1s ≈ 5 seconds before giving up.
_MAX_ATTEMPTS = 50
_RETRY_DELAY_S = 0.1


@contextmanager
def file_lock(lock_path: Path) -> Iterator[None]:
    """Hold an exclusive cross-process lock on ``lock_path`` for the block.

    Acquires a non-blocking ``LOCK_EX`` on a sidecar lock file, retrying for
    ~5 seconds before raising ``RuntimeError``. Always releases on exit.
    """
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a") as f:
        for _ in range(_MAX_ATTEMPTS):
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as e:
                if e.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
                    raise
                time.sleep(_RETRY_DELAY_S)
        else:
            raise RuntimeError(f"could not acquire {lock_path.name} after 5s")
        try:
            yield
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
