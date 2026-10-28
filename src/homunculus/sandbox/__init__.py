"""The sandbox: where the agent's commands run, apart from the operator's machine.

A sandbox runs one command at a time and reports what it printed and how it ended.
`docker.py` is the one implementation today. Another backend only has to run a command
the same way.
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    output: str  # what the command printed, its errors mixed in as they came
    timed_out: bool  # stopped because it ran past its time


class Sandbox(Protocol):
    def run(self, command: str, timeout_seconds: float) -> CommandResult:
        """Run `command` with bash in the sandbox, stopping it after `timeout_seconds`."""
        ...
