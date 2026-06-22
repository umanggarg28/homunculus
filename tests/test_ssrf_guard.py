"""SSRF guard on the web tools.

An autonomous agent that reads untrusted web content can be
prompt-injected into requesting its own infrastructure: the unauth'd
web API on localhost:8765, docker-internal services, or cloud metadata
endpoints. _url_block_reason rejects non-http(s) schemes and any host
that resolves to a non-global address; web_fetch re-validates every
redirect hop so a public URL can't 302 into the internal network.

DNS resolution is monkeypatched — no network in tests.
"""

import importlib
import socket
import sys
import types
from pathlib import Path

import pytest

# conftest stubs `tools` as a flat module, so `tools.web` can't be
# imported the normal way. Register a parallel REAL package pointing at
# the tools/ directory; relative imports (._helpers) resolve within it.
if "tools_real" not in sys.modules:
    _pkg = types.ModuleType("tools_real")
    _pkg.__path__ = [str(Path(__file__).parent.parent / "homunculus" / "tools")]
    sys.modules["tools_real"] = _pkg

_web = importlib.import_module("tools_real.web")
_url_block_reason = _web._url_block_reason
web_fetch = _web.web_fetch


@pytest.fixture
def fake_dns(monkeypatch):
    """Map hostnames to fixed IPs; unknown hosts raise gaierror."""
    table = {
        "public.example": "93.184.216.34",
        "evil-rebind.example": "10.0.0.7",
        "metadata-alias.example": "169.254.169.254",
        "localhost": "127.0.0.1",
    }

    def fake_getaddrinfo(host, port, *args, **kwargs):
        import ipaddress
        # Literal IPs (v4 and v6) resolve to themselves, like the real
        # resolver.
        try:
            ipaddress.ip_address(host)
            ip = host
        except ValueError:
            if host not in table:
                raise socket.gaierror(f"unknown host {host}") from None
            ip = table[host]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 80))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.delenv("HOMUNCULUS_ALLOW_PRIVATE_URLS", raising=False)
    return table


def test_public_host_allowed(fake_dns):
    assert _url_block_reason("https://public.example/page") is None


@pytest.mark.parametrize("url", [
    "http://localhost:8765/api/tasks",        # the agent's own web API
    "http://127.0.0.1/admin",
    "http://169.254.169.254/latest/meta-data/",  # cloud metadata
    "http://10.0.0.1/internal",
    "http://192.168.1.1/router",
    "http://[::1]:8080/",
])
def test_private_and_loopback_addresses_blocked(fake_dns, url):
    reason = _url_block_reason(url)
    assert reason is not None and reason.startswith("ERROR")
    assert "non-public address" in reason


def test_private_resolving_hostname_blocked(fake_dns):
    """The host LOOKS public but resolves into the private network."""
    reason = _url_block_reason("https://evil-rebind.example/x")
    assert reason is not None and "non-public address" in reason
    reason = _url_block_reason("https://metadata-alias.example/x")
    assert reason is not None and "non-public address" in reason


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "ftp://public.example/file",
    "gopher://public.example/",
])
def test_non_http_schemes_blocked(fake_dns, url):
    reason = _url_block_reason(url)
    assert reason is not None and "not allowed" in reason


def test_unresolvable_host_blocked(fake_dns):
    reason = _url_block_reason("https://no-such-host.example/")
    assert reason is not None and "could not resolve" in reason


def test_escape_hatch_for_local_dev(fake_dns, monkeypatch):
    monkeypatch.setenv("HOMUNCULUS_ALLOW_PRIVATE_URLS", "1")
    assert _url_block_reason("http://localhost:8765/") is None


def test_web_fetch_blocks_redirect_into_private_network(fake_dns, monkeypatch):
    """Public URL 302s to localhost — the per-hop re-validation must
    catch the second hop. This is the classic validate-once bypass."""
    import httpx

    def fake_get(url, **kwargs):
        request = httpx.Request("GET", url)
        if "public.example" in str(url):
            return httpx.Response(
                302,
                headers={"location": "http://127.0.0.1:8765/api/tasks"},
                request=request,
            )
        raise AssertionError(f"unexpected fetch of {url}")

    monkeypatch.setattr(httpx, "get", fake_get)
    result = web_fetch("https://public.example/redirect-me")
    assert result.startswith("ERROR")
    assert "non-public address" in result
