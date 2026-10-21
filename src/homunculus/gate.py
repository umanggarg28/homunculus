"""The gate: the one place that decides whether a proposed action may run."""

import json
from enum import Enum
from pathlib import PurePosixPath

from homunculus.model import ToolCall


class Answer(Enum):
    ACT = "act"  # run it without asking
    ACT_WHEN_ASKED = "act when asked"  # run it only if the operator asked for this action
    ASK = "ask"  # wait for the operator's approval
    HAND_OFF = "hand off"  # the operator must do this themselves
    REFUSE = "refuse"  # never run it


# Tools that only touch files inside the agent's workspace, and the arguments that name a path.
WORKSPACE_FILE_TOOLS = {"read", "write", "edit", "list", "search", "delete", "rename"}
PATH_ARGUMENTS = ("path", "to")


def decide(call: ToolCall) -> Answer:
    """Answer a proposed tool call. Any failure to decide is a refusal."""
    try:
        return _decide(call)
    except Exception:
        return Answer.REFUSE


def _decide(call: ToolCall) -> Answer:
    arguments = json.loads(call.arguments)
    if not isinstance(arguments, dict):
        return Answer.REFUSE
    if call.name in WORKSPACE_FILE_TOOLS:
        if not isinstance(arguments.get("path"), str):
            return Answer.REFUSE
        for name in PATH_ARGUMENTS:
            path = arguments.get(name)
            if path is not None and (not isinstance(path, str) or not _stays_in_workspace(path)):
                return Answer.REFUSE
        return Answer.ACT
    return Answer.ASK


def _stays_in_workspace(path: str) -> bool:
    """A relative path with no '..' in it. The file tools also resolve symbolic links,
    which only they can see, so a path that passes here can still be refused there."""
    parts = PurePosixPath(path)
    return not parts.is_absolute() and ".." not in parts.parts
