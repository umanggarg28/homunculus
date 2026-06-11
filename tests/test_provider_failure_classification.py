"""Provider-failure classification — the 2026-06-11 9 AM incident.

Timeline of the live failure this guards against: the primary model was
throttled, the chain moved to OpenRouter's kimi free slug which had
gone paid-only overnight. Its 404 body ("This model is unavailable for
free...") didn't match the old "no endpoints" substring check, so
call_llm RAISED with two healthy fallback providers unused. The
heartbeat then classified the RuntimeError as a real task failure,
which advanced the daily task's due_at a full day — silently skipping
that day's delivery.

Two layers fixed, both tested here:
  1. core: ANY 404 from a provider is a this-provider problem →
     cool it and try the next provider, never raise.
  2. heartbeat: provider/network exception strings mark the task
     PARTIAL (retry ~10 min), not failed (skip to next occurrence).
"""

import sys
import types

import httpx

if "tools.notify" not in sys.modules:
    _stub = types.ModuleType("tools.notify")
    _telegram_calls: list[str] = []
    _stub._send_to_telegram = lambda text: _telegram_calls.append(text) or None
    _stub._telegram_calls = _telegram_calls
    sys.modules["tools.notify"] = _stub

from core import _is_transient_provider_error  # noqa: E402
from heartbeat import _is_infra_error  # noqa: E402


def _resp(status: int, body: str = "") -> httpx.Response:
    return httpx.Response(status, text=body, request=httpx.Request("POST", "https://x/v1/chat"))


# ---- core: 404 means "try the next provider" -------------------------------

def test_404_paywalled_slug_is_transient():
    r = _resp(404, '{"error":{"message":"This model is unavailable for free. '
                   'The paid version is available now - use this slug instead: x/y"}}')
    assert _is_transient_provider_error(r)


def test_404_no_endpoints_still_transient():
    assert _is_transient_provider_error(_resp(404, '{"error":"No endpoints found"}'))


def test_5xx_transient_and_auth_errors_still_raise():
    for status in (502, 503, 504):
        assert _is_transient_provider_error(_resp(status))
    # 401/403 (bad key) must still raise — retrying other providers
    # with the same broken config would mask a real setup problem.
    assert not _is_transient_provider_error(_resp(401, "bad key"))
    assert not _is_transient_provider_error(_resp(403, "forbidden"))


# ---- heartbeat: infra errors are partials, not failures ---------------------

def test_provider_api_errors_classified_as_infra():
    assert _is_infra_error("RuntimeError: API error 404: model unavailable")
    assert _is_infra_error("RuntimeError: All providers exhausted: {...}")
    assert _is_infra_error("ConnectTimeout: timed out")
    assert _is_infra_error("ReadTimeout: read operation timed out")


def test_task_and_code_bugs_still_record_real_failures():
    assert not _is_infra_error("KeyError: 'task_id'")
    assert not _is_infra_error("ValueError: recurrence must be one of [...]")
    assert not _is_infra_error("TypeError: unsupported operand")
