"""Telegram listener survives an unreachable Telegram (regional block).

run_polling() initializes via get_me(), which raises TimedOut when
api.telegram.org is unreachable. The listener must wait and retry with backoff
instead of crashing — otherwise Docker restarts it in a tight loop. A bad token
(InvalidToken/Forbidden) is a misconfiguration, not an outage, and must fail
fast. Outbound notifications are unaffected (tools.notify degrades to Discord +
web feed).
"""

from __future__ import annotations

import pytest
from telegram.error import Forbidden, InvalidToken, NetworkError, TimedOut

from homunculus.transports.telegram import run_polling_with_retry


class _FakeApp:
    """Stands in for a python-telegram-bot Application; run_polling() runs the
    injected behavior (raise to simulate a failure, return for clean exit)."""

    def __init__(self, behavior):
        self._behavior = behavior

    def run_polling(self):
        return self._behavior()


def test_retries_on_timeout_then_exits_cleanly():
    calls = {"n": 0}
    sleeps: list[int] = []
    resets = {"n": 0}

    def behavior():
        calls["n"] += 1
        if calls["n"] < 3:
            raise TimedOut("blocked")
        return None  # third attempt connects, run_polling returns (clean exit)

    run_polling_with_retry(
        lambda: _FakeApp(behavior),
        base_backoff=1,
        sleep=lambda s: sleeps.append(s),
        reset_loop=lambda: resets.__setitem__("n", resets["n"] + 1),
    )

    # Two failures then a clean return — three build/poll attempts total.
    assert calls["n"] == 3
    # Backoff doubled between the two retries (1 → 2).
    assert sleeps == [1, 2]
    # A fresh event loop was installed before every attempt (else the 2nd
    # run_polling would hit 'Event loop is closed').
    assert resets["n"] == 3


def test_network_error_is_retried():
    calls = {"n": 0}

    def behavior():
        calls["n"] += 1
        if calls["n"] < 2:
            raise NetworkError("connection reset")
        return None

    run_polling_with_retry(
        lambda: _FakeApp(behavior), base_backoff=1,
        sleep=lambda s: None, reset_loop=lambda: None,
    )
    assert calls["n"] == 2


def test_backoff_caps_at_max():
    sleeps: list[int] = []
    attempts = {"n": 0}

    def behavior():
        attempts["n"] += 1
        if attempts["n"] > 8:
            return None
        raise TimedOut("still blocked")

    run_polling_with_retry(
        lambda: _FakeApp(behavior),
        base_backoff=10,
        max_backoff=40,
        reset_loop=lambda: None,
        sleep=lambda s: sleeps.append(s),
    )
    # 10, 20, 40, 40, 40, ... never exceeds the cap.
    assert max(sleeps) == 40
    assert sleeps[:4] == [10, 20, 40, 40]


def test_clean_exit_does_not_retry():
    calls = {"n": 0}

    def behavior():
        calls["n"] += 1
        return None  # immediate clean shutdown

    run_polling_with_retry(
        lambda: _FakeApp(behavior), sleep=lambda s: None, reset_loop=lambda: None,
    )
    assert calls["n"] == 1


@pytest.mark.parametrize("fatal", [InvalidToken("bad token"), Forbidden("revoked")])
def test_misconfiguration_fails_fast_not_retried(fatal):
    """A bad/revoked token is a misconfiguration, not an outage — it must raise
    and stop, never spin in the retry loop hiding the real problem."""
    calls = {"n": 0}
    sleeps: list[int] = []

    def behavior():
        calls["n"] += 1
        raise fatal

    with pytest.raises(type(fatal)):
        run_polling_with_retry(
            lambda: _FakeApp(behavior),
            sleep=lambda s: sleeps.append(s),
            reset_loop=lambda: None,
        )
    # Tried exactly once, then re-raised — no backoff sleep.
    assert calls["n"] == 1
    assert sleeps == []
