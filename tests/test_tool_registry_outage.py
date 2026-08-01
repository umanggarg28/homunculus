"""Empty tool registry = tool server down → fail loud, not limp.

Live outage 2026-07-29→08-01: a Docker rebuild without uv.lock pulled
mcp 2.0.0 (breaking API), the builtin MCP server crashed at import, and
the registry stayed EMPTY for three days. Every scheduled run flailed
against "Available tools: ." — 30 partials, 0 completions, LLM budget
burned, and the user saw only per-task escalation spam that blamed
"provider limits or task is broken".

Empty and unreadable are opposite signals: unreadable → introspection
hiccup, capability gate fails open; empty → nothing can succeed, the
tick skips task runs (tasks stay due and fire when tools return) and
the user is told once per outage what actually broke.
"""

import sys
import types

if "homunculus.tools.notify" not in sys.modules or not hasattr(
    sys.modules["homunculus.tools.notify"], "deliver"
):
    _stub = types.ModuleType("homunculus.tools.notify")
    _calls: list = []
    _stub._deliver_calls = _calls
    _stub.deliver = lambda text: (_calls.append(text), {"recorded": True})[1]
    _stub._send_to_telegram = lambda *_a, **_kw: None
    sys.modules["homunculus.tools.notify"] = _stub

from homunculus import heartbeat  # noqa: E402
import homunculus.tools as tools_module  # noqa: E402


def _deliver_calls():
    return sys.modules["homunculus.tools.notify"]._deliver_calls


def test_empty_registry_detected(monkeypatch):
    monkeypatch.setattr(tools_module, "SCHEMAS", [], raising=False)
    assert heartbeat._tool_registry_empty() is True


def test_populated_registry_is_healthy(monkeypatch):
    monkeypatch.setattr(
        tools_module, "SCHEMAS",
        [{"function": {"name": "notify"}}], raising=False,
    )
    assert heartbeat._tool_registry_empty() is False


def test_unreadable_registry_is_not_treated_as_outage(monkeypatch):
    # Introspection hiccup → fail open (the capability gate's contract).
    class Boom:
        def __getattr__(self, _):
            raise RuntimeError("introspection broke")
    monkeypatch.setattr(heartbeat, "tools", Boom())
    assert heartbeat._tool_registry_empty() is False


def test_alert_fires_once_per_outage(monkeypatch):
    monkeypatch.setattr(heartbeat, "_registry_alert_sent", False)
    _deliver_calls().clear()
    heartbeat._alert_tool_registry_down_once()
    heartbeat._alert_tool_registry_down_once()
    assert len(_deliver_calls()) == 1
    assert "tool server" in _deliver_calls()[0]
