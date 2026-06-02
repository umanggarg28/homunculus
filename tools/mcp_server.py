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

import datetime as _dt
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


@mcp.tool(annotations={"readOnlyHint": False})
def append_file(
    path: Annotated[str, Field(description="Path to append to. Created if it doesn't exist.")],
    content: Annotated[str, Field(description="Text to append.")],
) -> str:
    """Append text to a file without overwriting existing content. Creates the file if needed."""
    return filesystem.append_file(path, content)


@mcp.tool(annotations={"readOnlyHint": True})
def list_files(
    path: Annotated[str, Field(description="Directory to list. Defaults to workspace root '.'.")] = ".",
) -> str:
    """List files and subdirectories at the given path in the workspace.

    Use this to discover what files exist before reading or writing.
    Skips hidden files, cache, and node_modules.
    """
    return filesystem.list_files(path)


@mcp.tool(annotations={"readOnlyHint": True})
def search_files(
    query: Annotated[str, Field(description="Text or regex to search for.")],
    path: Annotated[str, Field(description="Directory to search under. Defaults to workspace root '.'.")] = ".",
    case_sensitive: Annotated[bool, Field(description="Case-sensitive match. Default false.")] = False,
) -> str:
    """Search (grep) for text across all workspace files. Returns matching lines with file:line references.

    Use this to find specific content, function names, or keywords across the workspace
    without reading every file individually.
    """
    return filesystem.search_files(query, path, case_sensitive)


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


@mcp.tool(annotations={"readOnlyHint": True})
def recall(
    query: Annotated[
        str,
        Field(
            description=(
                "Keywords to search long-term memory for. Be specific — use "
                "names, topics, or phrases you expect to appear in the memory "
                "body. Returns up to 3 matching snippets with age tags."
            )
        ),
    ],
) -> str:
    """Search long-term memory for relevant entries matching a query.

    Call this when you need facts the user may have shared in a previous
    session, when the index hints at a relevant memory but you need its
    full body, or when the user asks what you know or remember about them.
    Nothing is injected automatically — you decide when to recall.
    """
    return memory_tools.search_memory(query)


import json as _json


@mcp.tool(annotations={"readOnlyHint": True})
def get_world_state() -> str:
    """Read the current session world state.

    Returns a JSON object tracking what you are working on right now:
    focus, active_task, step, last_action, last_ok, notes.
    Call at the start of a multi-step task to check if prior steps already
    completed (e.g. after a restart or interruption).
    """
    state = memory_tools.get_world_state()
    if not state:
        return "{}"
    return _json.dumps(state, indent=2)


@mcp.tool(annotations={"readOnlyHint": False})
def update_world_state(
    updates: Annotated[
        dict,
        Field(
            description=(
                "Key-value pairs to merge into the world state. "
                "Conventional keys: focus (str), active_task (str), step (int), "
                "last_action (str), last_ok (bool), notes (str). "
                "Omitted keys are preserved; pass null to clear a key."
            )
        ),
    ],
) -> str:
    """Update the session world state with new key-value pairs.

    Call this to track progress through multi-step tasks so the agent can
    resume safely after interruption, and the UI can show live status.
    Safe to call frequently — writes are atomic and cheap.
    """
    state = memory_tools.update_world_state(updates)
    return "World state updated: " + _json.dumps(state)


@mcp.tool(annotations={"readOnlyHint": False})
def rate_skill(
    name: Annotated[str, Field(description="Name of the skill memory to rate (partial match ok).")],
    outcome: Annotated[
        Literal["success", "failure"],
        Field(description="Whether this use of the skill achieved the intended result."),
    ],
    notes: Annotated[
        str,
        Field(description="Optional one-line note about what worked or failed."),
    ] = "",
) -> str:
    """Record whether a skill worked this time.

    Call after completing a task that used a learned skill. Increments the
    skill's use counter and tracks consecutive failures. After 3 failures the
    skill is flagged for review in the daily reflection.
    Do NOT call for every task — only for tasks that explicitly relied on a
    skill_*.md procedure.
    """
    return memory_tools.rate_skill(name, outcome, notes)


# ── datetime ─────────────────────────────────────────────────────────


def _resolve_tz(timezone: str):
    """Return (ZoneInfo, error_str). error_str is None on success."""
    try:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        return ZoneInfo(timezone), None
    except ImportError:
        return None, "ERROR: zoneinfo not available (requires Python 3.9+)"
    except (Exception,):
        return None, (
            f"Unknown timezone '{timezone}'. "
            "Use IANA names like 'Asia/Tokyo', 'America/New_York', 'Europe/London', 'UTC'."
        )


@mcp.tool(annotations={"readOnlyHint": True})
def get_current_time(
    timezones: Annotated[
        list[str],
        Field(
            description=(
                "One or more IANA timezone names. Pass ALL needed timezones in one call "
                "instead of calling this multiple times. "
                "Examples: ['Asia/Tokyo'], ['Asia/Tokyo','Europe/London','America/New_York']. "
                "Never pass an empty list."
            )
        ),
    ],
) -> str:
    """Return the current date and time for one or more IANA timezones in a single call.

    Use this for ANY time/timezone question — it is instant and accurate.
    Pass all requested timezones together so one call handles the whole query.
    Do NOT use web_search for time queries.
    """
    if not timezones:
        return "ERROR: 'timezones' list is empty — pass at least one IANA timezone name."
    lines = []
    for tz_name in timezones:
        tz, err = _resolve_tz(tz_name)
        if err:
            lines.append(f"{tz_name}: ERROR — {err}")
        else:
            now = _dt.datetime.now(tz=tz)
            lines.append(f"{tz_name}: {now.strftime('%Y-%m-%d %H:%M:%S %Z (UTC%z)')}")
    return "\n".join(lines)


# ── web ───────────────────────────────────────────────────────────────


@mcp.tool(annotations={"readOnlyHint": True})
def web_search(
    query: Annotated[str, Field(description="Search query.")],
) -> str:
    """Search the web. Returns a DIRECT ANSWER (use this first) plus supporting snippets. Cite full URLs in your reply."""
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
    code: Annotated[str, Field(description="Python source to execute. Must print results to stdout — return values are not captured.")],
) -> str:
    """Execute Python in a sandboxed subprocess (no network, 30s timeout, ephemeral filesystem).

    Use this ONLY when you need to actually run code to obtain a result (compute a value,
    parse data, test a function). Do NOT call this just to show or display code to the user —
    write code blocks directly in your reply text instead.
    """
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
    """Create a new task (reminder, recurring job, or one-shot). IMPORTANT: Before calling this, always call list_tasks(status='all') and check if a task with a similar title already exists. If one does, use schedule_task() or update the existing task instead of creating a duplicate."""
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
    """Reschedule an EXISTING task. Requires a task_id from list_tasks(). To create a new reminder, use create_task() instead."""
    return scheduling.schedule_task(task_id, due_at, recurrence)


# ── notify ────────────────────────────────────────────────────────────


@mcp.tool(annotations={"readOnlyHint": False})
def notify(
    text: Annotated[
        str,
        Field(
            description=(
                "Plain-text message. Sent to Telegram, which does NOT render "
                "markdown the way the chat UI does. Do NOT use heading hashes "
                "(##), `**bold**`, or markdown tables — they show as literal "
                "characters. Use short sentences, line breaks, and bullet "
                "lines starting with `- ` (we convert those to • automatically)."
            )
        ),
    ],
    preview: Annotated[
        bool,
        Field(
            description=(
                "When True, returns the rendered HTML body that WOULD be sent "
                "instead of pushing. Useful for rare cases where you want to "
                "double-check formatting before interrupting the user. Default "
                "is False — most calls should just send."
            ),
        ),
    ] = False,
) -> str:
    """Push a plain-text Telegram message. INTERRUPTS the user — use only for time-sensitive things. Routine summaries belong in files."""
    return notify_mod.notify(text, preview=preview)


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
