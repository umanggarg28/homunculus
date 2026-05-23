"""Built-in MCP server (FastMCP) exposing Homunculus's tools over stdio.

Runs as a standalone process: `python -m tools.mcp_server`. The manager
(`tools/mcp_manager.py`) launches it as a subprocess and talks to it
over stdio — the same transport used for every external MCP server, so
the builtin gets no special treatment.

Implementation: each tool is a small `@mcp.tool()`-decorated wrapper
that delegates to a plain Python function in the per-category modules
(`filesystem`, `memory_tools`, `web`, `sandbox`, `scheduling`, `notify`).
The wrapper is where the LLM-facing schema lives:
    - Description = docstring.
    - Per-parameter description = `Annotated[T, Field(description=...)]`.
    - Read/write hints = `annotations={"readOnlyHint": ...}` — the
      manager reads these to decide plan-mode policy.

This keeps the schemas in one place, rich, and SOTA-aligned with how
modern Claude / GPT models consume tool catalogs.
"""

from __future__ import annotations

from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from . import filesystem, memory_tools, notify as notify_mod, sandbox, scheduling, web


mcp = FastMCP("homunculus-builtin")


# ── filesystem ────────────────────────────────────────────────────────


@mcp.tool(annotations={"readOnlyHint": True})
def read_file(
    path: Annotated[str, Field(description="Path to the file.")],
) -> str:
    """Read a UTF-8 text file from disk and return its contents."""
    return filesystem.read_file(path)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
def write_file(
    path: Annotated[str, Field(description="Path to write.")],
    content: Annotated[str, Field(description="Text content.")],
) -> str:
    """Write text to a file (overwrites). Creates parent dirs."""
    return filesystem.write_file(path, content)


# ── memory ────────────────────────────────────────────────────────────


@mcp.tool(annotations={"readOnlyHint": False})
def remember(
    name: Annotated[str, Field(description="Short title for the memory.")],
    description: Annotated[str, Field(description="One-line summary used in the index.")],
    type: Annotated[
        Literal["user", "feedback", "project", "reference", "skill"],
        Field(
            description=(
                "Category of memory. 'skill' = a learned procedure (how to do X), "
                "distinct from 'feedback' (a rule) or 'project' (current state)."
            )
        ),
    ],
    body: Annotated[str, Field(description="Full content of the memory.")],
    related: Annotated[
        list[str] | None,
        Field(description="Optional. Related memory slugs (filenames without .md)."),
    ] = None,
) -> str:
    """Save a durable fact to long-term memory (persists across sessions). Not for ephemeral state."""
    return memory_tools.remember(name=name, description=description, type=type, body=body, related=related)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
def forget(
    name: Annotated[str, Field(description="Memory name or filename (with or without .md).")],
) -> str:
    """Delete a memory by name or filename. Use sparingly — when in doubt, leave it."""
    return memory_tools.forget(name)


# ── web ───────────────────────────────────────────────────────────────


@mcp.tool(annotations={"readOnlyHint": True})
def web_search(
    query: Annotated[str, Field(description="Search query.")],
) -> str:
    """Search the web. Returns ~5 results + summary. Cite full URLs in your reply when you use results."""
    return web.web_search(query)


@mcp.tool(annotations={"readOnlyHint": True})
def web_fetch(
    url: Annotated[str, Field(description="URL to fetch.")],
) -> str:
    """Fetch a URL and return its main text. Capped ~16K chars."""
    return web.web_fetch(url)


# ── sandbox ───────────────────────────────────────────────────────────


@mcp.tool(name="python", annotations={"readOnlyHint": False})
def python_tool(
    code: Annotated[str, Field(description="Python source to execute. Print results to stdout.")],
) -> str:
    """Run Python in a sandbox (no network, 30s, ephemeral). Returns stdout/stderr. Pass data via the code itself."""
    return sandbox.python_exec(code)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
def shell_exec(
    command: Annotated[str, Field(description="Shell command to run.")],
) -> str:
    """Run a shell command. User must approve each invocation."""
    return sandbox.shell_exec(command)


# ── scheduling ────────────────────────────────────────────────────────


@mcp.tool(annotations={"readOnlyHint": False})
def schedule_next_tick(
    iso_datetime: Annotated[str, Field(description="Target wake time, ISO 8601, local timezone.")],
) -> str:
    """Set heartbeat's next wake time. ISO 8601 local datetime, within 24h. Heartbeat-only. For recurring intent prefer create_task(recurrence=...)."""
    return scheduling.schedule_next_tick(iso_datetime)


@mcp.tool(annotations={"readOnlyHint": False})
def create_task(
    title: Annotated[str, Field(description="Short title for the task.")],
    description: Annotated[str, Field(description="Optional detail.")] = "",
    due_at: Annotated[
        str | None,
        Field(description="Optional ISO local datetime, e.g. 2026-05-18T09:00:00+05:30."),
    ] = None,
    recurrence: Annotated[
        Literal["none", "daily", "weekly"],
        Field(description="How often to repeat after completion."),
    ] = "none",
    notify: Annotated[bool, Field(description="Whether the due task is expected to notify the user.")] = False,
) -> str:
    """Create structured task state for reminders or recurring heartbeat work. Use this for any 'every day' / 'every week' commitment."""
    return scheduling.create_task(title, description, due_at, recurrence, notify)


@mcp.tool(annotations={"readOnlyHint": True})
def list_tasks(
    status: Annotated[
        Literal["active", "completed", "cancelled", "all"],
        Field(description="Filter by status."),
    ] = "active",
) -> str:
    """List structured tasks by status."""
    return scheduling.list_tasks(status)


@mcp.tool(annotations={"readOnlyHint": False})
def complete_task(
    task_id: Annotated[str, Field(description="Task id to complete.")],
    result: Annotated[str, Field(description="Optional result note.")] = "",
) -> str:
    """Mark a task complete. Recurring tasks advance to their next due time."""
    return scheduling.complete_task(task_id, result)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
def cancel_task(
    task_id: Annotated[str, Field(description="Task id to cancel.")],
    reason: Annotated[str, Field(description="Optional reason.")] = "",
) -> str:
    """Cancel a structured task that is no longer relevant."""
    return scheduling.cancel_task(task_id, reason)


@mcp.tool(annotations={"readOnlyHint": False})
def schedule_task(
    task_id: Annotated[str, Field(description="Task id to schedule.")],
    due_at: Annotated[str, Field(description="ISO local datetime.")],
    recurrence: Annotated[
        Literal["none", "daily", "weekly"] | None,
        Field(description="Optional new recurrence."),
    ] = None,
) -> str:
    """Set a task's due time and optionally recurrence."""
    return scheduling.schedule_task(task_id, due_at, recurrence)


# ── notify ────────────────────────────────────────────────────────────


@mcp.tool(annotations={"readOnlyHint": False})
def notify(
    text: Annotated[str, Field(description="Message text. Keep concise.")],
) -> str:
    """Push a Telegram message. INTERRUPTS the user — use only for time-sensitive things. Routine summaries belong in files."""
    return notify_mod.notify(text)


# ── entrypoint ────────────────────────────────────────────────────────


def main() -> None:
    # Builtin tools need workspace state (memory, autonomous flag)
    # initialised before the server starts serving requests. The
    # manager passes config via env vars.
    import os
    from pathlib import Path
    from memory import Memory
    from ._state import init as init_state

    memory_dir = Path(os.environ.get("HOMUNCULUS_MEMORY_DIR", "./memory"))
    autonomous = os.environ.get("HOMUNCULUS_AUTONOMOUS", "0") == "1"
    init_state(memory=Memory(memory_dir), autonomous=autonomous, mode="build")

    mcp.run("stdio")


if __name__ == "__main__":
    main()
