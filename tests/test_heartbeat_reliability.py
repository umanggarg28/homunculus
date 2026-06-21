"""Heartbeat reliability: transient-network retry classification.

Regression: a single DNS hiccup at 07:53 IST sent the heartbeat into a
full 60-minute backoff. Combined with a laptop suspend, the user lost
the entire morning's deliveries. Transient network errors should retry
in 60s, not 3600s.
"""

from __future__ import annotations

import sys
import types

# tools.notify stub so heartbeat imports work in test isolation
if "homunculus.tools.notify" not in sys.modules:
    _stub = types.ModuleType("homunculus.tools.notify")
    _stub._send_to_telegram = lambda *_a, **_kw: None
    sys.modules["homunculus.tools.notify"] = _stub

import httpx  # noqa: E402

from homunculus.heartbeat import _is_transient_network_error  # noqa: E402


def test_dns_resolution_failure_is_transient():
    err = httpx.ConnectError("[Errno -3] Temporary failure in name resolution")
    assert _is_transient_network_error(err)


def test_connect_timeout_is_transient():
    err = httpx.ConnectTimeout("connect timed out")
    assert _is_transient_network_error(err)


def test_connection_refused_is_transient():
    err = OSError("Connection refused")
    assert _is_transient_network_error(err)


def test_chained_exception_is_caught_via_cause():
    """httpx wraps httpcore.ConnectError — walking __cause__ must find it."""
    inner = ConnectionError("[Errno -3] Temporary failure in name resolution")
    outer = RuntimeError("LLM call failed")
    try:
        try:
            raise inner
        except ConnectionError as e:
            raise outer from e
    except RuntimeError as e:
        assert _is_transient_network_error(e)


def test_value_error_is_not_transient():
    """Programmer errors don't get the fast-retry path."""
    assert not _is_transient_network_error(ValueError("bad config"))


def test_4xx_message_is_not_transient():
    """Auth/quota failures take the long backoff — they don't self-heal."""
    err = RuntimeError("HTTP 401 Unauthorized: invalid API key")
    assert not _is_transient_network_error(err)


def test_random_runtime_error_is_not_transient():
    assert not _is_transient_network_error(RuntimeError("something broke"))
