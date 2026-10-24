"""The gate: the one place that decides whether a proposed action may run."""

import json
from enum import Enum
from pathlib import PurePosixPath

import mcp_types as mcp

from homunculus.model import ToolCall
from homunculus.tools import Tool


class Answer(Enum):
    ACT = "act"  # run it without asking
    ACT_WHEN_ASKED = "act when asked"  # run it only if the operator asked for this action
    ASK = "ask"  # wait for the operator's approval
    HAND_OFF = "hand off"  # the operator must do this themselves
    REFUSE = "refuse"  # never run it


# Actions only the operator should take. The agent may prepare them; the operator does them.
HAND_OFF_ACTIONS = {"pay", "enter_password", "enter_verification_code"}

# Destructive tools the operator allows without asking, because they only change the
# agent's own workspace and saving notes there is everyday work (decided 24 Oct 2026).
# rename never loses content, so it stays allowed as before. Every other destructive
# action, such as delete, asks.
ALLOWED_DESTRUCTIVE = {"write", "edit", "rename"}


def decide(call: ToolCall, tool: Tool | None) -> Answer:
    """Answer a proposed tool call, given the definition of the tool it names, or None
    when no such tool exists. Any failure to decide is a refusal."""
    try:
        return _decide(call, tool)
    except Exception:
        return Answer.REFUSE


def _decide(call: ToolCall, tool: Tool | None) -> Answer:
    arguments = json.loads(call.arguments)
    if not isinstance(arguments, dict):
        return Answer.REFUSE
    if call.name in HAND_OFF_ACTIONS:
        return Answer.HAND_OFF
    if tool is None:
        return Answer.REFUSE
    for name in tool.workspace_paths:
        path = arguments.get(name)
        if path is None and name not in tool.required:
            continue
        if not isinstance(path, str) or not _stays_in_workspace(path):
            return Answer.REFUSE
    # The annotations are trusted because every tool so far is the program's own. A tool
    # from an outside MCP server must not be judged by its own annotations (#9).
    hints = tool.definition.annotations or mcp.ToolAnnotations()
    if hints.read_only_hint is True:
        return Answer.ACT
    # A missing hint takes the protocol's default: outward, and destructive.
    if hints.open_world_hint is not False:
        return Answer.ASK
    if hints.destructive_hint is not False and call.name not in ALLOWED_DESTRUCTIVE:
        return Answer.ASK
    return Answer.ACT


def _stays_in_workspace(path: str) -> bool:
    """A relative path with no '..' in it. The file tools also resolve symbolic links,
    which only they can see, so a path that passes here can still be refused there."""
    parts = PurePosixPath(path)
    return not parts.is_absolute() and ".." not in parts.parts
