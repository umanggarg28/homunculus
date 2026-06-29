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


def load_tool(name: str) -> str:
    """Add a tool's full schema to the active set for this session.

    Idempotent. Returns a confirmation string the LLM sees as the
    tool result. The actual mutation is performed by the Agent's
    pre_execute_hook which intercepts the call BEFORE this function
    runs — so the side-effect lands before this string is returned.
    """
    # Forward-compat hook: if a tool registry is ever injected on _state, use it
    # to validate the name; otherwise this stays a no-op (the Agent's hook owns
    # the real active-set mutation).
    _known = getattr(_state, "get_known_tool_names", None)
    available: set[str] = cast("set[str]", _known()) if callable(_known) else set()
    if available and name not in available:
        return (
            f"ERROR: no tool named '{name}'. Available tools are listed "
            f"in your system prompt under 'Loadable tools'. Names are "
            f"case-sensitive."
        )
    return (
        f"Loaded tool '{name}'. Its full schema is in effect on the next "
        f"LLM call, so call it normally on your next turn."
    )
