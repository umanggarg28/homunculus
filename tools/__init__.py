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

import concurrent.futures
import os
from typing import Callable

from ._state import get_mode, init as _init_state, set_mode
from . import mcp_manager as _mgr_mod

# Optional hook installed by the heartbeat guard (or tests). Called before every
# tool execution. If it returns a non-None string, that string is returned to the
# agent instead of running the real tool — the guard can block a call and explain why.
_pre_execute_hook: Callable[[str, dict], str | None] | None = None


def set_pre_execute_hook(fn: Callable[[str, dict], str | None] | None) -> None:
    global _pre_execute_hook
    _pre_execute_hook = fn


# Item 5 of the robustness plan — turn-level hook. Called at the START of
# each iteration of the agent loop (before the LLM call). Receives the
# 0-indexed turn number. Returns either None (do nothing) or a synthetic
# message dict to append to history before the LLM is called. Used to:
#   - inject the harness budget nudge at MAX_TURNS-2
#   - force a complete_task/record_failure call at MAX_TURNS-1 when the
#     TaskGuard sees expected_remaining() is non-empty
#   - (future) per-tick context budget enforcement, summarisation triggers
# A single hook is fine; the loop hook implementation chains multiple
# concerns by composing them in the registered function.
_pre_turn_hook: Callable[[int, list], dict | None] | None = None


def set_pre_turn_hook(fn: Callable[[int, list], dict | None] | None) -> None:
    """Install a hook called at the start of each loop iteration.

    Hook signature: fn(turn_idx: int, history: list[dict]) -> dict | None
        Return a single message dict to append to history before the LLM
        is called, or None for a no-op turn.

    Pass None to clear the hook. Always clear after the tick to avoid
    leaking state between sessions.
    """
    global _pre_turn_hook
    _pre_turn_hook = fn

_manager = _mgr_mod.manager
_started = False

# Per-tool wall-clock timeout. Without this, a hung tool (DNS hang,
# blocked HTTP, deadlocked subprocess) freezes the entire agent loop
# indefinitely. Override via HOMUNCULUS_TOOL_TIMEOUT_S.
DEFAULT_TOOL_TIMEOUT_S = float(os.environ.get("HOMUNCULUS_TOOL_TIMEOUT_S", "60"))

# Hard cap on the tool-result string we feed back into history. Without
# a cap, a chatty tool (HTML dump, giant log file) can push us past the
# LLM context limit and 413 the next call. The truncation marker tells
# the LLM what happened so it can ask for less or retry differently.
TOOL_RESULT_MAX_CHARS = int(os.environ.get("HOMUNCULUS_TOOL_RESULT_MAX_CHARS", "8000"))

# Single shared executor so we don't spin up a thread per tool call.
_tool_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="tool-exec"
)


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

    # Output guard hook — installed by heartbeat TaskGuard (and tests).
    # If the hook returns a string, treat it as the tool result without
    # actually running the tool (blocks the call).
    if _pre_execute_hook is not None:
        blocked = _pre_execute_hook(name, arguments)
        if blocked is not None:
            return blocked

    if get_mode() == "plan" and _manager.is_mutating(name):
        args_preview = ", ".join(f"{k}={_short(v)}" for k, v in arguments.items())
        return (
            f"BLOCKED (plan mode): {name}({args_preview}) was not executed. "
            f"You are in plan mode — describe to the user what you would do "
            f"and what they should approve, then wait. Do not retry the same "
            f"call; switch to build mode if action is needed."
        )
    try:
        future = _tool_executor.submit(_manager.call, name, arguments)
        result = future.result(timeout=DEFAULT_TOOL_TIMEOUT_S)
    except concurrent.futures.TimeoutError:
        # The tool call is still running in the worker thread but the
        # agent loop has given up. We can't cancel an in-flight MCP
        # call without ripping the connection, so we return the timeout
        # error and let the worker finish in the background (it'll get
        # GC'd once it returns).
        return (
            f"ERROR: tool '{name}' exceeded {DEFAULT_TOOL_TIMEOUT_S:.0f}s timeout. "
            f"It may still be running. Try a different approach or "
            f"narrower arguments."
        )
    except Exception as e:
        result = f"ERROR running {name}: {type(e).__name__}: {e}"
    # Coerce non-string returns so json.dumps over history doesn't blow
    # up later (some tool implementations return bytes / dicts by accident).
    if not isinstance(result, str):
        try:
            result = str(result)
        except Exception:
            result = "ERROR: tool returned an unrepresentable value"
    # Truncate to keep the LLM context bounded.
    if len(result) > TOOL_RESULT_MAX_CHARS:
        original_len = len(result)
        dropped = original_len - TOOL_RESULT_MAX_CHARS
        result = (
            result[:TOOL_RESULT_MAX_CHARS]
            + f"\n\n[... truncated {dropped} chars; result was {original_len} bytes total]"
        )
    return result


__all__ = [
    "init", "execute", "get_mode", "set_mode",
    "SCHEMAS", "tool_names", "set_pre_execute_hook", "set_pre_turn_hook",
]


# Back-compat: some old code may do `from tools import init` and
# expect to also call the lower-level `_state.init`. They get the
# combined entry point above; underscore alias kept for clarity.
