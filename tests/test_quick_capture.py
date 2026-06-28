"""NEW.5 — iOS Shortcut quick-capture endpoint.

Focused on the auth + rate-limit surface. The single-turn agent path
is exercised end-to-end by manual testing (LLM call is unstubbable
without a fixture rig that doesn't exist for this codebase yet).
"""

from __future__ import annotations

import importlib
import sys
import types
from unittest.mock import patch

from fastapi.testclient import TestClient


def _load_app():
    if "tools" not in sys.modules:
        tools_stub = types.ModuleType("tools")
        tools_stub.SCHEMAS = []
        tools_stub.init = lambda *a, **k: None
        tools_stub.get_mode = lambda: "build"
        tools_stub.set_mode = lambda mode: None
        tools_stub.set_pre_execute_hook = lambda h: None
        tools_stub.set_pre_turn_hook = lambda h: None
        sys.modules["homunculus.tools"] = tools_stub
    web_api = importlib.import_module("homunculus.transports.web_api")
    return web_api


def _capture_mod():
    """The quick-capture rate bucket/constants live on the capture router."""
    return importlib.import_module("homunculus.transports.web.capture")


def test_quick_capture_refuses_without_token_configured():
    web_api = _load_app()
    with patch.object(web_api, "QUICK_CAPTURE_TOKEN", ""):
        client = TestClient(web_api.app)
        r = client.post(
            "/api/quick-capture",
            headers={"X-Capture-Token": "anything"},
            json={"text": "hi"},
        )
        assert r.status_code == 503


def test_quick_capture_rejects_wrong_token():
    web_api = _load_app()
    with patch.object(web_api, "QUICK_CAPTURE_TOKEN", "real-token"):
        client = TestClient(web_api.app)
        r = client.post(
            "/api/quick-capture",
            headers={"X-Capture-Token": "wrong"},
            json={"text": "hi"},
        )
        assert r.status_code == 401


def test_quick_capture_rejects_missing_text():
    web_api = _load_app()
    with patch.object(web_api, "QUICK_CAPTURE_TOKEN", "real-token"):
        client = TestClient(web_api.app)
        r = client.post(
            "/api/quick-capture",
            headers={"X-Capture-Token": "real-token"},
            json={"text": "   "},
        )
        assert r.status_code == 400


def test_quick_capture_rejects_oversized_text():
    web_api = _load_app()
    with patch.object(web_api, "QUICK_CAPTURE_TOKEN", "real-token"):
        client = TestClient(web_api.app)
        r = client.post(
            "/api/quick-capture",
            headers={"X-Capture-Token": "real-token"},
            json={"text": "x" * 3000},
        )
        assert r.status_code == 400


def test_quick_capture_rate_limit():
    web_api = _load_app()
    # Reset the shared rate bucket before exercising it.
    _capture_mod()._QUICK_CAPTURE_RATE.clear()
    with patch.object(web_api, "QUICK_CAPTURE_TOKEN", "real-token"), \
         patch.object(web_api, "Agent") as MockAgent:
        # Stub the LLM round-trip — we're testing the gate, not the agent.
        MockAgent.return_value.chat.return_value = "ok"
        client = TestClient(web_api.app)
        for _ in range(_capture_mod()._QUICK_CAPTURE_RATE_MAX):
            r = client.post(
                "/api/quick-capture",
                headers={"X-Capture-Token": "real-token"},
                json={"text": "test"},
            )
            assert r.status_code == 200, r.text
        r = client.post(
            "/api/quick-capture",
            headers={"X-Capture-Token": "real-token"},
            json={"text": "one too many"},
        )
        assert r.status_code == 429


def test_quick_capture_returns_agent_reply():
    web_api = _load_app()
    _capture_mod()._QUICK_CAPTURE_RATE.clear()
    with patch.object(web_api, "QUICK_CAPTURE_TOKEN", "real-token"), \
         patch.object(web_api, "Agent") as MockAgent:
        MockAgent.return_value.chat.return_value = "Created task: Call dentist Fri 15:00 IST."
        client = TestClient(web_api.app)
        r = client.post(
            "/api/quick-capture",
            headers={"X-Capture-Token": "real-token"},
            json={"text": "remind me to call the dentist Friday at 3pm"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body == {"ok": True, "reply": "Created task: Call dentist Fri 15:00 IST."}
        # Source tag must propagate to the agent so the unified log
        # can distinguish ios-shortcut captures from web/telegram turns.
        call_kwargs = MockAgent.return_value.chat.call_args
        assert call_kwargs.kwargs.get("source") == "ios-shortcut"
