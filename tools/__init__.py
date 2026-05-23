"""Tools package — public API for the agent runtime.

External surface unchanged:
    init(memory, autonomous, mode)   — wire up state at startup
    SCHEMAS                          — the LLM-facing tool catalog
    execute(name, args)              — dispatch a tool call by name
    get_mode() / set_mode(mode)      — plan/build runtime mode

Implementation: every tool lives in an MCP server (built-in or
external) launched as its own subprocess. `tools/mcp_manager.py` owns
the connections; this module is a thin policy layer on top — it adds
the plan-mode check, exposes the schemas the manager built, and routes
`execute()` through the manager.

There is no Python-level dispatch table here. The set of available
tools is whatever the configured MCP servers expose on connect.
"""

from __future__ import annotations

from ._state import get_mode, init as _init_state, set_mode
from . import mcp_manager as _mgr_mod

_manager = _mgr_mod.manager
_started = False


def init(memory, autonomous: bool = False, mode: str = "build") -> None:
    """Wire up state AND start the MCP manager.

    Must be called exactly once at process startup from the parent
    (REPL / FastAPI / heartbeat). The builtin MCP server subprocess
    does NOT call this — it initialises its own `_state` directly to
    avoid spawning the manager recursively.
    """
    global _started
    _init_state(memory=memory, autonomous=autonomous, mode=mode)
    if not _started:
        _manager.start()
        _started = True


def _short(value: object, limit: int = 60) -> str:
    s = repr(value)
    return s if len(s) <= limit else s[: limit - 1] + "…"


class _SchemasProxy(list):
    """List that re-reads from the manager on every access.

    The LLM-facing schema list changes when the YAML config reloads,
    so we don't cache. The proxy lets older code that does
    `from tools import SCHEMAS` still see the live list without
    refactoring every importer.
    """

    def __iter__(self):
        return iter(_manager.schemas())

    def __len__(self):
        return len(_manager.schemas())

    def __getitem__(self, i):
        return _manager.schemas()[i]

    def __repr__(self):
        return repr(_manager.schemas())


SCHEMAS = _SchemasProxy()


def tool_names() -> set[str]:
    return _manager.tool_names()


def execute(name: str, arguments: dict) -> str:
    """Dispatch a tool call by name.

    Errors are caught and stringified so a broken tool turns into
    information the LLM can recover from, not a crashed agent loop.

    In plan mode, mutating tools are refused with a structured message
    telling the LLM what would have happened. "Mutating" is declared
    per-server in `homunculus.yaml` — not hardcoded here.
    """
    if name not in _manager.tool_names():
        return f"ERROR: unknown tool '{name}'"
    if get_mode() == "plan" and _manager.is_mutating(name):
        args_preview = ", ".join(f"{k}={_short(v)}" for k, v in arguments.items())
        return (
            f"BLOCKED (plan mode): {name}({args_preview}) was not executed. "
            f"You are in plan mode — describe to the user what you would do "
            f"and what they should approve, then wait. Do not retry the same "
            f"call; switch to build mode if action is needed."
        )
    try:
        return _manager.call(name, arguments)
    except Exception as e:
        return f"ERROR running {name}: {type(e).__name__}: {e}"


__all__ = [
    "init", "execute", "get_mode", "set_mode",
    "SCHEMAS", "tool_names",
]


# Back-compat: some old code may do `from tools import init` and
# expect to also call the lower-level `_state.init`. They get the
# combined entry point above; underscore alias kept for clarity.
