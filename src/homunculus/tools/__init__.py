"""Tools the agent can use. Each one is a plain function called in this process."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


class ToolError(Exception):
    """A tool could not do what was asked. The message is shown to the model."""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema for the arguments
    run: Callable[[dict[str, Any]], str]

    def call(self, arguments: dict[str, Any]) -> str:
        """Run the tool after checking the model supplied every required argument."""
        for name in self.parameters.get("required", []):
            if name not in arguments:
                raise ToolError(f"{self.name} needs the argument '{name}'.")
        return self.run(arguments)

    def schema(self) -> dict[str, Any]:
        """The tool as the model sees it, in the Chat Completions format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
