"""web_post — POST sibling of web_fetch for endpoints that need a body.

Built so the refinement agent can verify GraphQL queries and REST POSTs
without going through the network-isolated python sandbox. Live LeetCode
refinement on Haiku tried `python(code=...urlopen POST...)` and hit
--network=none — this is the surfaced primitive that closes that gap.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# conftest stubs `tools` as a flat module to keep MCP deps optional.
# Load tools/_helpers.py and tools/web.py as real submodules under the
# canonical names so `from ._helpers import ...` resolves correctly.
def _load_real_tool_submodule(name: str):
    src = Path(__file__).parent.parent / "tools" / f"{name}.py"
    full = f"tools.{name}"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(full, src)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    if "tools" in sys.modules and not hasattr(sys.modules["tools"], "__path__"):
        sys.modules["tools"].__path__ = [str(src.parent)]  # type: ignore[attr-defined]
    sys.modules[full] = mod
    spec.loader.exec_module(mod)
    return mod


_load_real_tool_submodule("_helpers")  # web.py imports from ._helpers
_web = _load_real_tool_submodule("web")
web_post = _web.web_post


def _mk_response(*, status_code=200, text="", content_type="application/json"):
    r = MagicMock()
    r.status_code = status_code
    r.text = text
    r.headers = {"content-type": content_type}
    return r


# ---- happy paths ------------------------------------------------------


def test_json_body_sent_as_application_json() -> None:
    """Most calls (especially GraphQL) want JSON body. Verify the
    underlying httpx.post is called with json= kwarg, which httpx
    serializes + sets Content-Type."""
    with patch.object(_web.httpx, "post", return_value=_mk_response(text='{"ok":true}')) as mock_post:
        web_post("https://example.com/graphql", json_body={"query": "{ hi }"})
    assert mock_post.called
    _, kwargs = mock_post.call_args
    assert kwargs["json"] == {"query": "{ hi }"}
    assert "User-Agent" in kwargs["headers"]
    assert kwargs["timeout"] == 30.0


def test_returns_response_text() -> None:
    with patch.object(_web.httpx, "post", return_value=_mk_response(text='{"data":{"x":1}}')):
        out = web_post("https://example.com/api", json_body={})
    assert out == '{"data":{"x":1}}'


def test_extra_headers_merged() -> None:
    with patch.object(_web.httpx, "post", return_value=_mk_response()) as mock_post:
        web_post("https://example.com", json_body={}, headers={"Authorization": "Bearer x"})
    _, kwargs = mock_post.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer x"
    assert "User-Agent" in kwargs["headers"], "User-Agent default must still be set"


def test_raw_body_takes_precedence_over_json_body() -> None:
    """When the caller needs a non-JSON content type they set raw_body
    + Content-Type header. raw_body must win."""
    with patch.object(_web.httpx, "post", return_value=_mk_response()) as mock_post:
        web_post(
            "https://example.com",
            json_body={"ignored": True},
            raw_body="key=value&other=thing",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    _, kwargs = mock_post.call_args
    assert "json" not in kwargs
    assert kwargs["content"] == b"key=value&other=thing"


def test_empty_post_when_no_body_given() -> None:
    """Some endpoints accept POST with no body (signal-only calls).
    Send an empty JSON object rather than crashing."""
    with patch.object(_web.httpx, "post", return_value=_mk_response()) as mock_post:
        web_post("https://example.com/ping")
    _, kwargs = mock_post.call_args
    assert kwargs["json"] == {}


# ---- error handling ---------------------------------------------------


def test_http_error_returns_error_string() -> None:
    import httpx as real_httpx
    with patch.object(
        _web.httpx, "post",
        side_effect=real_httpx.ConnectError("network down"),
    ):
        out = web_post("https://example.com", json_body={})
    assert out.startswith("ERROR: POST failed:")


def test_403_returns_blocked_message() -> None:
    """Anti-bot 403 — same shape as web_fetch's BLOCKED, includes the
    URL so the agent knows which call failed."""
    with patch.object(_web.httpx, "post", return_value=_mk_response(status_code=403)):
        out = web_post("https://example.com/api", json_body={})
    assert "BLOCKED" in out
    assert "403" in out
    assert "example.com/api" in out


def test_429_returns_blocked_message() -> None:
    with patch.object(_web.httpx, "post", return_value=_mk_response(status_code=429)):
        out = web_post("https://example.com", json_body={})
    assert "BLOCKED" in out and "429" in out


def test_400_returns_status_plus_body() -> None:
    """GraphQL and REST validation errors often arrive as 400 with the
    error explanation in the body — surface both so the agent can use
    them to refine the query."""
    body = '{"errors": [{"message":"Unknown argument planSlug"}]}'
    with patch.object(_web.httpx, "post", return_value=_mk_response(status_code=400, text=body)):
        out = web_post("https://example.com/graphql", json_body={"query": "..."})
    assert "HTTP 400" in out
    assert "planSlug" in out


def test_500_returns_status_plus_body() -> None:
    with patch.object(_web.httpx, "post", return_value=_mk_response(status_code=500, text="oops")):
        out = web_post("https://example.com", json_body={})
    assert "HTTP 500" in out
    assert "oops" in out


# ---- response shaping -------------------------------------------------


def test_long_response_truncated_with_marker() -> None:
    """Same cap as web_fetch (config.loop.read_file_max_chars). The
    truncation marker is mandatory so the agent knows it's seeing a
    prefix, not the full payload."""
    huge = "X" * 50_000
    with patch.object(_web.httpx, "post", return_value=_mk_response(text=huge)):
        out = web_post("https://example.com", json_body={})
    assert "chars truncated" in out
    assert len(out) < len(huge)


def test_html_response_stripped_to_text() -> None:
    """Some endpoints reply HTML on success (rare but real for some
    SOAP-shaped APIs). Strip to text so the agent doesn't drown in
    tags."""
    html = "<html><body><h1>Hello</h1><script>bad()</script></body></html>"
    with patch.object(_web.httpx, "post", return_value=_mk_response(text=html, content_type="text/html")):
        out = web_post("https://example.com", json_body={})
    assert "Hello" in out
    assert "<script>" not in out
    assert "<h1>" not in out


# ---- realistic GraphQL shape ------------------------------------------


def test_graphql_query_shape_round_trip() -> None:
    """Matches the actual call shape the refinement agent would use to
    verify leetcode.com/graphql. Body is {"query": "..."}; response
    parses cleanly as JSON."""
    response_json = '{"data":{"studyPlanV2Detail":{"name":"Top Interview 150"}}}'
    with patch.object(_web.httpx, "post", return_value=_mk_response(text=response_json)) as mock_post:
        out = web_post(
            "https://leetcode.com/graphql/",
            json_body={"query": "query { studyPlanV2Detail(planSlug: \"top-interview-150\") { name } }"},
        )
    _, kwargs = mock_post.call_args
    assert kwargs["json"]["query"].startswith("query")
    assert "Top Interview 150" in out
