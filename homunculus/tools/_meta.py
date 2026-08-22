"""Meta-tools — tools whose effect is on the harness itself.

`load_tool` is the entry point for the dynamic tool-loading pattern:
on each turn the agent's LLM only sees the schemas in `ALWAYS_LOADED`
plus whatever was loaded this session. Anything else is listed by
name + description in the system prompt; calling load_tool("name")
adds it to the active set and the next call includes the full schema.

This module does NOT perform the schema lookup itself — that belongs
to the Agent instance which owns the per-session active set. The
function here just validates the name and returns a confirmation
string; the actual mutation happens via the pre_execute_hook the
Agent installs (see core.py:_install_tool_loader).
"""

from __future__ import annotations

from typing import cast

from . import _state


def no_action(reason: str) -> str:
    """Record a deliberate decision to do nothing this turn."""
    reason = (reason or "").strip()
    if not reason:
        return (
            "ERROR: no_action requires a reason — say what you checked and "
            "why nothing needs doing. An unexplained no-op is indistinguishable "
            "from giving up."
        )
    return f"No action taken, by decision: {reason}"


def load_tool(name: str | list[str]) -> str:
    """Add a tool's full schema to the active set for this session.

    Idempotent. Returns a confirmation string the LLM sees as the
    tool result. The actual mutation is performed by the Agent's
    pre_execute_hook which intercepts the call BEFORE this function
    runs — so the side-effect lands before this string is returned.
    """
    # Forward-compat hook: if a tool registry is ever injected on _state, use it
    # to validate the name; otherwise this stays a no-op (the Agent's hook owns
    # the real active-set mutation).
    requested = [n.strip() for n in ([name] if isinstance(name, str) else list(name)) if str(n).strip()]
    if not requested:
        return "ERROR: load_tool needs a tool name, or a list of them."
    _known = getattr(_state, "get_known_tool_names", None)
    available: set[str] = cast("set[str]", _known()) if callable(_known) else set()
    if available:
        unknown = [n for n in requested if n not in available]
        if unknown:
            return (
                f"ERROR: no tool named {', '.join(repr(u) for u in unknown)}. "
                f"Available tools are listed in your system prompt under "
                f"'Loadable tools'. Names are case-sensitive."
            )
    listed = ", ".join(f"'{n}'" for n in requested)
    return (
        f"Loaded {'tool' if len(requested) == 1 else 'tools'} {listed}. "
        f"{'Its' if len(requested) == 1 else 'Their'} full schema is in effect "
        f"on the next LLM call, so call {'it' if len(requested) == 1 else 'them'} "
        f"normally on your next turn."
    )
