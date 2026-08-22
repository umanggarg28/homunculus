"""Two costs the loop was paying every day, both structural.

`no_action` — the reflection prompt says "if the delivery is genuinely good →
no action", and the turn runs under tool_choice=required. There was no tool
that means "nothing to do", so a correct review had no legal move: the model
answered in prose, the harness recorded `required_tool_violation` twice, and
the turn died. Observed on three consecutive days, with the model's prose
reading "no skill edits proposed — all 7 deliveries were genuinely good".

`load_tool` batching — one tool per call, and each call costs a whole model
round-trip before any work happens. It was the most-called tool in
production (98 calls, ahead of read_file's 63) against a $5/month ceiling.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent.parent / "homunculus" / "tools"


def _load_by_path(name: str, path: Path):
    """Import a tools submodule directly, bypassing the conftest stub that
    replaces `homunculus.tools` with an empty module for the whole suite.

    Registered under a real package name so the module's own relative
    imports (`from . import _state`) resolve.
    """
    import sys
    import types

    pkg_name = "tools_real_pkg"
    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [str(_TOOLS)]
        sys.modules[pkg_name] = pkg
    full = f"{pkg_name}.{name}"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(full, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[full] = mod
    spec.loader.exec_module(mod)
    return mod


_meta = _load_by_path("_meta", _TOOLS / "_meta.py")

#: Read from source rather than imported: the package __init__ pulls in the
#: MCP stack, which is container-only.
ALWAYS_LOADED = {
    line.strip().strip('",')
    for line in (_TOOLS / "__init__.py").read_text()
    .split("ALWAYS_LOADED: frozenset[str] = frozenset({", 1)[1]
    .split("})", 1)[0]
    .splitlines()
    if line.strip().startswith('"')
}


class TestNoAction:
    def test_is_always_loaded(self):
        """It must never require a load_tool call to reach: a forced turn
        cannot spend its one tool call on loading the escape hatch."""
        assert "no_action" in ALWAYS_LOADED

    def test_records_the_reason(self):
        out = _meta.no_action("Reviewed all 7 deliveries; every one grounded.")
        assert "No action taken" in out
        assert "7 deliveries" in out

    def test_refuses_an_unexplained_no_op(self):
        """Without a reason it is indistinguishable from giving up."""
        assert _meta.no_action("").startswith("ERROR")
        assert _meta.no_action("   ").startswith("ERROR")


class TestLoadToolBatching:
    def test_single_name_still_works(self):
        """The existing calling convention must not break."""
        out = _meta.load_tool("get_weather")
        assert "get_weather" in out and "ERROR" not in out

    def test_list_loads_several_at_once(self):
        out = _meta.load_tool(["get_weather", "news_headlines"])
        assert "get_weather" in out and "news_headlines" in out
        assert "ERROR" not in out

    def test_empty_request_is_refused(self):
        assert _meta.load_tool([]).startswith("ERROR")
        assert _meta.load_tool("  ").startswith("ERROR")

    def test_unknown_names_are_named_back(self, monkeypatch):
        monkeypatch.setattr(
            _meta._state, "get_known_tool_names",
            lambda: {"get_weather"}, raising=False,
        )
        out = _meta.load_tool(["get_weather", "not_a_tool"])
        assert out.startswith("ERROR") and "not_a_tool" in out
        # The valid one is not silently loaded behind a failure.
        assert "Loaded" not in out


class TestLoopActivatesBatchedTools:
    def test_a_list_activates_every_named_tool(self, monkeypatch):
        """The Agent, not the tool function, owns the active-set mutation."""
        from homunculus import core

        # The suite-wide conftest stub leaves SCHEMAS empty, so the loop's
        # schema check would reject every call before dispatch. This test is
        # about the activation step that follows it.
        monkeypatch.setattr(core, "_validate_tool_args", lambda n, a: None)
        agent = core.Agent(memory=None)
        agent._active_tool_names = set()
        monkeypatch.setattr(
            core.tools, "tool_names",
            lambda: {"get_weather", "news_headlines", "task_health_summary"},
            raising=False,
        )
        monkeypatch.setattr(core.tools, "execute", lambda n, a: "ok", raising=False)

        import json
        agent._dispatch_tool_calls(
            [{
                "id": "c1",
                "function": {
                    "name": "load_tool",
                    "arguments": json.dumps(
                        {"name": ["get_weather", "news_headlines"]}
                    ),
                },
            }],
            set(), {}, {}, [],
        )
        assert {"get_weather", "news_headlines"} <= agent._active_tool_names

    def test_a_bare_string_still_activates(self, monkeypatch):
        from homunculus import core

        monkeypatch.setattr(core, "_validate_tool_args", lambda n, a: None)
        agent = core.Agent(memory=None)
        agent._active_tool_names = set()
        monkeypatch.setattr(
            core.tools, "tool_names", lambda: {"get_weather"}, raising=False
        )
        monkeypatch.setattr(core.tools, "execute", lambda n, a: "ok", raising=False)

        import json
        agent._dispatch_tool_calls(
            [{
                "id": "c1",
                "function": {
                    "name": "load_tool",
                    "arguments": json.dumps({"name": "get_weather"}),
                },
            }],
            set(), {}, {}, [],
        )
        assert "get_weather" in agent._active_tool_names
