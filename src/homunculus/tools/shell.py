"""The shell tool: a bash command, run in the sandbox."""

from typing import Any

import mcp_types as mcp

from homunculus.sandbox import Sandbox
from homunculus.tools import Tool, ToolError

# The same limits Claude Code's own shell tool uses.
DEFAULT_TIMEOUT_MS = 120_000
MAX_TIMEOUT_MS = 600_000
MAX_OUTPUT = 30_000

# Honest by the Model Context Protocol's definitions: a command can change or delete
# anything in the workspace, and can download from the package registries. The gate
# lets it act anyway, because it runs in the sandbox.
ANNOTATIONS = mcp.ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=True
)


def shell_tool(sandbox: Sandbox) -> Tool:
    definition = mcp.Tool(
        name="shell",
        title="Run a command",
        description=(
            "Run a bash command in your sandbox: a Linux container with Python 3.12, "
            "whose only shared folder is your workspace, at /workspace, the folder you "
            "start in. Use it to run code, scripts and tests, and to install packages "
            "with pip or npm. The package registries are the only sites it can reach. "
            "It returns what the command printed and its exit code. Long output is "
            "shortened in the middle."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The bash command to run."},
                "timeout": {
                    "type": "integer",
                    "description": (
                        f"Milliseconds to allow, {DEFAULT_TIMEOUT_MS} if not given, "
                        f"{MAX_TIMEOUT_MS} at most."
                    ),
                },
            },
            "required": ["command"],
        },
        annotations=ANNOTATIONS,
    )

    def run(arguments: dict[str, Any]) -> str:
        command = arguments["command"]
        if not isinstance(command, str):
            raise ToolError("the command must be text.")
        timeout_ms = min(int(arguments.get("timeout") or DEFAULT_TIMEOUT_MS), MAX_TIMEOUT_MS)
        seconds = timeout_ms / 1000
        result = sandbox.run(command, timeout_seconds=seconds)
        output = _shortened(result.output, result.omitted)
        if output and not output.endswith("\n"):
            output += "\n"
        if result.timed_out:
            raise ToolError(f"{output}[stopped after {seconds:g} s: the command took too long]")
        text = f"{output}[exit code {result.exit_code}]"
        if result.exit_code != 0:
            raise ToolError(text)
        return text

    return Tool(definition, run, runs_in_sandbox=True)


def _shortened(output: str, omitted: int) -> str:
    """The output, or its start and end when it is too long to show whole. Bytes the
    sandbox never kept are counted as characters, so that count is "about"."""
    if len(output) <= MAX_OUTPUT and not omitted:
        return output
    keep = min(MAX_OUTPUT // 2, len(output) // 2)
    left_out = len(output) - 2 * keep + omitted
    count = f"about {left_out}" if omitted else str(left_out)
    return f"{output[:keep]}\n[{count} characters left out]\n{output[len(output) - keep :]}"
