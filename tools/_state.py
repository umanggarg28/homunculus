"""Shared state for the tools package.

Tools need access to a Memory instance and the autonomous-mode flag.
Rather than threading those through every call, we keep them in this
module — `init()` (re-exported from `tools.__init__`) sets them once at
startup, and every tool reads via the helpers below.
"""

from __future__ import annotations

from memory import Memory


_memory: Memory | None = None
_autonomous: bool = False
# "build" → full tool access. "plan" → read-only mode; mutating tools
# are refused with a message telling the agent what they would have
# done. Inspired by opencode's plan/build dual-agent split.
_mode: str = "build"


def init(memory: Memory, autonomous: bool = False, mode: str = "build") -> None:
    """Wire up the Memory instance and runtime mode.

    autonomous=True means there's no human at the keyboard to approve
    interactive prompts (shell_exec, etc.). Such tools refuse and tell
    the LLM to leave a note for the user instead.

    mode="plan" disables mutating tools — useful for "look at things
    and suggest" workflows where you don't want the agent to take
    irreversible action.
    """
    global _memory, _autonomous, _mode
    _memory = memory
    _autonomous = autonomous
    set_mode(mode)


def get_memory() -> Memory | None:
    return _memory


def is_autonomous() -> bool:
    return _autonomous


def get_mode() -> str:
    return _mode


def set_mode(mode: str) -> None:
    global _mode
    if mode not in {"build", "plan"}:
        raise ValueError(f"mode must be 'build' or 'plan', got {mode!r}")
    _mode = mode
