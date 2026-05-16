"""
Tools for Homunculus.

A "tool" is two things glued together:
  1. A JSON schema that tells the LLM how to call it (name, args, types).
  2. A Python function that actually runs when the LLM calls it.

We register tools in a single dict (TOOLS) so the agent loop can
look them up by name.
"""

import subprocess
from pathlib import Path

from memory import Memory


# --- Dependency injection -------------------------------------------------
# Some tools need access to a Memory instance. Rather than threading it
# through every call, we store it at module level and let main.py
# initialize it once at startup with `tools.init(memory)`.

_memory: Memory | None = None
_autonomous: bool = False


def init(memory: Memory, autonomous: bool = False) -> None:
    """Wire up the Memory instance and runtime mode.

    autonomous=True means there's no human at the keyboard to approve
    shell commands. shell_exec will refuse and tell the LLM to leave a
    note for the user instead. Read/write/remember still work normally.
    """
    global _memory, _autonomous
    _memory = memory
    _autonomous = autonomous


# --- Tool implementations -------------------------------------------------

def read_file(path: str) -> str:
    """Read a UTF-8 text file. Returns its contents as a string."""
    return Path(path).read_text(encoding="utf-8")


def write_file(path: str, content: str) -> str:
    """Write text to a file (overwrites). Creates parent dirs if missing."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Wrote {len(content)} bytes to {path}"


def remember(name: str, description: str, type: str, body: str) -> str:
    """Save a durable fact to long-term memory.

    Memory is read on every future session, so use this for things worth
    remembering across conversations: user preferences, project context,
    feedback rules, references. Not for ephemeral conversation state.
    """
    if _memory is None:
        return "ERROR: memory subsystem is not initialized"
    return _memory.remember(name=name, description=description, type=type, body=body)


def shell_exec(command: str) -> str:
    """Run a shell command.

    REPL mode: prompts the user to approve each call (y/N).
    Autonomous mode (heartbeat): refuses outright. The LLM is told to
    leave a note via remember() instead so the user can run it next
    REPL session.

    Safety boundary: we NEVER run shell commands unsupervised. Even
    inside the container, autonomous shell access invites trouble.
    """
    if _autonomous:
        return (
            "BLOCKED: shell_exec is disabled in autonomous (heartbeat) mode. "
            "If you really need this command run, call remember() to leave "
            "a note for the user describing what you want and why; they'll "
            "execute it next REPL session."
        )
    print(f"\n  [agent wants to run]: {command}")
    answer = input("  approve? [y/N]: ").strip().lower()
    if answer != "y":
        return "DENIED by user"
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=60
        )
    except subprocess.TimeoutExpired:
        return "ERROR: command timed out after 60s"
    output = (result.stdout + result.stderr).strip()
    if not output:
        return f"(exit {result.returncode}, no output)"
    # Cap output so a chatty command doesn't blow up our context window.
    if len(output) > 4000:
        return output[:4000] + "\n[...truncated]"
    return output


# --- Tool schemas (what the LLM sees) -------------------------------------
# These follow the OpenAI tool-use spec, which Groq uses verbatim.
# The LLM only sees this — names, descriptions, parameter types. So good
# descriptions matter: they're the LLM's user manual for our tools.

SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a UTF-8 text file from disk and return its contents.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file."}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write text to a file (overwrites). Creates parent dirs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to write."},
                    "content": {"type": "string", "description": "Text content."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": (
                "Save a durable fact to long-term memory. Use for things "
                "worth remembering across sessions (user preferences, "
                "project context, feedback rules, references). Not for "
                "ephemeral conversation state."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Short title for the memory."},
                    "description": {"type": "string", "description": "One-line summary used in the index."},
                    "type": {
                        "type": "string",
                        "enum": ["user", "feedback", "project", "reference"],
                        "description": "Category of memory.",
                    },
                    "body": {"type": "string", "description": "Full content of the memory."},
                },
                "required": ["name", "description", "type", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shell_exec",
            "description": "Run a shell command. User must approve each invocation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run."}
                },
                "required": ["command"],
            },
        },
    },
]


# Registry: name -> Python function. Keys MUST match the "name" fields above.
TOOLS = {
    "read_file": read_file,
    "write_file": write_file,
    "remember": remember,
    "shell_exec": shell_exec,
}


def execute(name: str, arguments: dict) -> str:
    """Look up a tool by name and run it with the LLM-provided arguments.

    Returns a string (the agent loop appends it as the tool-result message).
    Errors are caught and stringified — we don't want a tool exception to
    kill the whole agent.
    """
    if name not in TOOLS:
        return f"ERROR: unknown tool '{name}'"
    try:
        return str(TOOLS[name](**arguments))
    except Exception as e:
        return f"ERROR running {name}: {type(e).__name__}: {e}"
