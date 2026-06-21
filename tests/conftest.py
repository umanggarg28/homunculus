"""
Shared test fixtures and pre-import stubs.

Several modules (homunculus.tools, homunculus.transports.web_api) pull in deps that are only
available inside the Docker container (mcp, fastmcp). This conftest stubs
them out before any test module is imported, so tests that don't need the
real implementations can run locally without Docker.
"""

import sys
import types


def _stub_module(name: str, **attrs):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


# Stub MCP deps before tools/__init__.py tries to import them.
for _name in [
    "mcp",
    "mcp.types",
    "mcp.server",
    "mcp.server.fastmcp",
    "mcp.server.fastmcp.exceptions",
]:
    if _name not in sys.modules:
        _stub_module(_name)

# Stub the tools package so web_api and other importers get a working module.
# Tests that need the real tools module must reload it themselves.
if "homunculus.tools" not in sys.modules or not hasattr(sys.modules["homunculus.tools"], "init"):
    _tools_stub = _stub_module(
        "homunculus.tools",
        SCHEMAS=[],
        init=lambda *a, **k: None,
        get_mode=lambda: "build",
        set_mode=lambda mode: None,
        set_pre_execute_hook=lambda *a, **k: None,
        # Item 5 of the robustness plan added a turn-level hook. Tests
        # need an attribute they can monkey-patch — the no-op default
        # mirrors the real implementation's behavior.
        _pre_turn_hook=None,
        set_pre_turn_hook=lambda *a, **k: None,
    )

# Canonical tools.notify stub. heartbeat imports `deliver`; many test modules
# also install their own minimal stub (guarded on absence), so provide a
# complete one here once — with deliver + _send_to_telegram and capture lists —
# so those guards skip and every importer of heartbeat works, in isolation too.
if "homunculus.tools.notify" not in sys.modules or not hasattr(
    sys.modules["homunculus.tools.notify"], "deliver"
):
    _deliver_calls: list = []
    _telegram_calls: list = []

    def _stub_deliver(text):
        _deliver_calls.append(text)
        return {"recorded": True, "delivered": [], "failed": []}

    _stub_module(
        "homunculus.tools.notify",
        deliver=_stub_deliver,
        _deliver_calls=_deliver_calls,
        _send_to_telegram=lambda text: _telegram_calls.append(text) or None,
        _telegram_calls=_telegram_calls,
    )


def load_real_tool_submodule(name: str):
    """Load tools/<name>.py as the real module `tools.<name>`.

    The `tools` package is stubbed above to keep MCP deps optional, so
    tests that exercise real tool internals (web, watch, skill
    refinement) load just the submodules they need through this helper.
    Marking the stub with a __path__ lets relative imports inside the
    submodules (`from . import _helpers`) resolve normally.
    """
    import importlib.util
    from pathlib import Path

    src = Path(__file__).parent.parent / "homunculus" / "tools" / f"{name}.py"
    full = f"homunculus.tools.{name}"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(full, src)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    if "homunculus.tools" in sys.modules and not hasattr(sys.modules["homunculus.tools"], "__path__"):
        sys.modules["homunculus.tools"].__path__ = [str(src.parent)]  # type: ignore[attr-defined]
    sys.modules[full] = mod
    spec.loader.exec_module(mod)
    return mod
