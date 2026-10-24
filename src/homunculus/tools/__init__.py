"""Tools the agent can use. Each one is a plain function called in this process.

A tool is described the way the Model Context Protocol describes one, with the official
`mcp_types` package: a name, a description, a JSON Schema for its arguments, and
annotations saying what it does to its surroundings. The gate decides from those
annotations, so a tool's own definition is the one place its risk is written down.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import mcp_types as mcp


class ToolError(Exception):
    """A tool could not do what was asked. The message is shown to the model."""


@dataclass(frozen=True)
class Tool:
    definition: mcp.Tool
    run: Callable[[dict[str, Any]], str]
    # The arguments that name a path in the workspace, which the gate keeps inside it.
    workspace_paths: tuple[str, ...] = ()

    @property
    def name(self) -> str:
        return self.definition.name

    @property
    def required(self) -> list[str]:
        """The arguments the tool's schema marks as required."""
        return list(self.definition.input_schema.get("required", []))

    def call(self, arguments: dict[str, Any]) -> mcp.CallToolResult:
        """Run the tool after checking the model supplied every required argument. A
        failure the tool reports is a result with `is_error` set, never an exception."""
        try:
            for name in self.required:
                if name not in arguments:
                    raise ToolError(f"{self.name} needs the argument '{name}'.")
            return make_result(self.run(arguments))
        except ToolError as error:
            return make_result(str(error), is_error=True)

    def schema(self) -> dict[str, Any]:
        """The tool as the model provider expects it, in the Chat Completions format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.definition.description or "",
                "parameters": self.definition.input_schema,
            },
        }


def make_result(text: str, is_error: bool = False) -> mcp.CallToolResult:
    """A result holding one piece of text."""
    return mcp.CallToolResult(content=[mcp.TextContent(type="text", text=text)], is_error=is_error)


def result_text(result: mcp.CallToolResult) -> str:
    """The text of a result, as one string."""
    return "\n".join(item.text for item in result.content if isinstance(item, mcp.TextContent))
