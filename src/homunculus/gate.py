"""The gate: the one place that decides whether a proposed action may run."""

import json
from enum import Enum

from homunculus.model import ToolCall


class Answer(Enum):
    ACT = "act"  # run it without asking
    ACT_WHEN_ASKED = "act when asked"  # run it only if the operator asked for this action
    ASK = "ask"  # wait for the operator's approval
    HAND_OFF = "hand off"  # the operator must do this themselves
    REFUSE = "refuse"  # never run it


# Tools that only touch files inside the agent's workspace. The tools themselves
# refuse any path that leads outside it.
WORKSPACE_FILE_TOOLS = {"read", "write", "edit", "list", "search"}


def decide(call: ToolCall) -> Answer:
    """Answer a proposed tool call. Any failure to decide is a refusal."""
    try:
        return _decide(call)
    except Exception:
        return Answer.REFUSE


def _decide(call: ToolCall) -> Answer:
    arguments = json.loads(call.arguments)
    if call.name in WORKSPACE_FILE_TOOLS:
        if not isinstance(arguments.get("path"), str):
            return Answer.REFUSE
        return Answer.ACT
    return Answer.ASK
