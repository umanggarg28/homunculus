"""notify retry-on-transient-failure.

A single Telegram timeout used to discard a generated delivery (observed
2026-06-16: a quiz question lost to one 20s timeout). _send_to_telegram now
retries transient transport errors with short backoff before giving up.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

# Load tools/notify.py as a STANDALONE module (not under the shared
# sys.modules["homunculus.tools.notify"] key) so we don't clobber the stub that
# test_autonomous_fallback_notify installs there. notify.py has no relative
# imports, so it loads cleanly on its own.
_spec = importlib.util.spec_from_file_location(
    "notify_under_test", Path(__file__).parent.parent / "homunculus" / "tools" / "notify.py"
)
notify = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(notify)


@pytest.fixture(autouse=True)
def _telegram_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "123")
    # No real sleeping between retries.
    monkeypatch.setattr(notify.time, "sleep", lambda *_: None)


class _OK:
    status_code = 200


def test_retries_then_succeeds(monkeypatch):
    """Two transient timeouts, third attempt succeeds → delivered, no error."""
    calls = []

    def flaky_post(*a, **k):
        calls.append(1)
        if len(calls) < 3:
            raise httpx.ReadTimeout("timed out")
        return _OK()

    monkeypatch.setattr(notify, "_queue_for_telegram_history", lambda *_: None)
    with patch.object(notify.httpx, "post", side_effect=flaky_post):
        err = notify._send_to_telegram("hello")
    assert err is None
    assert len(calls) == 3  # retried twice, succeeded on the third


def test_gives_up_after_all_attempts(monkeypatch):
    """Every attempt times out → a single error after exhausting retries."""
    def always_timeout(*a, **k):
        raise httpx.ReadTimeout("timed out")

    with patch.object(notify.httpx, "post", side_effect=always_timeout):
        err = notify._send_to_telegram("hello")
    assert err is not None
    assert "after" in err and "attempts" in err


def test_no_retry_on_success(monkeypatch):
    """A clean first send does not retry."""
    calls = []

    def ok_post(*a, **k):
        calls.append(1)
        return _OK()

    monkeypatch.setattr(notify, "_queue_for_telegram_history", lambda *_: None)
    with patch.object(notify.httpx, "post", side_effect=ok_post):
        err = notify._send_to_telegram("hello")
    assert err is None
    assert len(calls) == 1
