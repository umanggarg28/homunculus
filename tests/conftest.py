"""
Shared test fixtures and pre-import stubs.

Several modules (tools, transports.web_api) pull in deps that are only
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
if "tools" not in sys.modules or not hasattr(sys.modules["tools"], "init"):
    _tools_stub = _stub_module(
        "tools",
        SCHEMAS=[],
        init=lambda *a, **k: None,
        get_mode=lambda: "build",
        set_mode=lambda mode: None,
        set_pre_execute_hook=lambda *a, **k: None,
    )
