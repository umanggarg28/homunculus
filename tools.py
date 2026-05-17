"""
Tools for Homunculus.

A "tool" is two things glued together:
  1. A JSON schema that tells the LLM how to call it (name, args, types).
  2. A Python function that actually runs when the LLM calls it.

We register tools in a single dict (TOOLS) so the agent loop can
look them up by name.
"""

import os
import subprocess
from pathlib import Path

import httpx

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

# How much of a file to return at most. Above this, we keep the TAIL
# (most recent content) and prepend a note. Keeps daily logs from blowing
# up the context window, which would otherwise feed back on itself —
# each heartbeat tick reads the log, writes more to the log, etc.
READ_FILE_MAX_CHARS = 16_000


def _normalize_workspace_path(path: str) -> str:
    """Defensive path normalization.

    The Docker container sets cwd to /app/workspace, so the agent's
    relative paths are already inside the workspace. But LLMs often
    still emit 'workspace/foo.md', which then becomes /app/workspace/
    workspace/foo.md — a nested mess. We strip those prefixes so the
    agent can be wrong about cwd and still land in the right place.
    """
    if path.startswith("/app/workspace/"):
        return path[len("/app/workspace/"):]
    if path.startswith("workspace/"):
        return path[len("workspace/"):]
    return path


def read_file(path: str) -> str:
    """Read a UTF-8 text file. Returns its contents, capped to the last
    READ_FILE_MAX_CHARS characters if the file is larger (tail preserved
    because for logs and chronological files, recent content is what
    matters)."""
    text = Path(_normalize_workspace_path(path)).read_text(encoding="utf-8")
    if len(text) <= READ_FILE_MAX_CHARS:
        return text
    truncated = text[-READ_FILE_MAX_CHARS:]
    omitted = len(text) - READ_FILE_MAX_CHARS
    return f"[...{omitted} chars omitted from start; showing tail...]\n\n{truncated}"


def write_file(path: str, content: str) -> str:
    """Write text to a file (overwrites). Creates parent dirs if missing."""
    p = Path(_normalize_workspace_path(path))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Wrote {len(content)} bytes to {p}"


def remember(
    name: str,
    description: str,
    type: str,
    body: str,
    related: list[str] | None = None,
) -> str:
    """Save a durable fact to long-term memory.

    Memory is read on every future session, so use this for things worth
    remembering across conversations: user preferences, project context,
    feedback rules, references. Not for ephemeral conversation state.

    `related` is an optional list of memory slugs (filenames without
    .md) that this memory connects to — e.g. ["user_role", "project_homunculus"].
    Renders as [[wikilinks]] for Obsidian graph view.
    """
    if _memory is None:
        return "ERROR: memory subsystem is not initialized"
    return _memory.remember(
        name=name, description=description, type=type, body=body, related=related,
    )


def schedule_next_tick(iso_datetime: str) -> str:
    """Tell the heartbeat daemon when you want to wake up next.

    Takes an ISO 8601 datetime string in the local timezone, e.g.
    "2026-05-18T08:00:00". Must be in the future, within 24h. Only
    meaningful when called from the heartbeat (other services ignore
    the scheduled time).

    If you don't call this, the heartbeat falls back to its default
    interval (HEARTBEAT_INTERVAL_MINUTES, typically 10 min).
    """
    if _memory is None:
        return "ERROR: memory subsystem is not initialized"
    # Validate before persisting so the agent gets immediate feedback if
    # it passed something malformed.
    from datetime import datetime, timedelta
    try:
        target = datetime.fromisoformat(iso_datetime)
    except ValueError:
        return (
            f"ERROR: '{iso_datetime}' is not a valid ISO 8601 datetime. "
            f"Format: YYYY-MM-DDTHH:MM:SS (e.g. 2026-05-18T08:00:00)."
        )
    now = datetime.now()
    if target <= now:
        return f"ERROR: target time {target} is in the past (now: {now})."
    if target > now + timedelta(hours=24):
        return (
            f"ERROR: target time {target} is more than 24h away. "
            f"Schedule something sooner; you can always re-schedule from "
            f"the next tick."
        )
    _memory.set_next_tick(iso_datetime)
    delta = target - now
    return f"Scheduled next heartbeat for {iso_datetime} (in {delta})."


def python_exec(code: str, timeout: int = 30) -> str:
    """Run Python code in an ephemeral sandbox container.

    Each call spawns a fresh python:3.12-slim sibling container via the
    host's Docker daemon (we have the socket mounted). Container is
    destroyed when it exits.

    Sandbox flags:
      --network=none   no internet, no exfiltration
      --memory=256m    RAM cap
      --cpus=0.5       CPU cap
      --pids-limit=50  no fork bombs
      --read-only      filesystem read-only (no persistence)
      --rm             auto-cleanup
      30s wall-clock timeout from our side

    Code is delivered via stdin; only stdout/stderr come back. The
    sandbox has no access to workspace/, memory/, or our network — it
    is a real isolation boundary, not just process separation.

    Use for: math, parsing, computation, verifying snippets, exploring
    data, running untrusted-ish code safely. Cannot persist files or
    reach the network.
    """
    cmd = [
        "docker", "run", "--rm", "-i",
        "--network=none",
        "--memory=256m",
        "--cpus=0.5",
        "--pids-limit=50",
        "--read-only",
        "--tmpfs", "/tmp:size=64m",  # /tmp needs to be writable for some libs
        "python:3.12-slim",
        "python", "-",
    ]
    try:
        result = subprocess.run(
            cmd,
            input=code,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"ERROR: code execution timed out after {timeout}s"
    except FileNotFoundError:
        return (
            "ERROR: docker CLI not found. The python_exec tool needs "
            "/var/run/docker.sock mounted and the docker CLI binary in "
            "the image. Check docker-compose.yml and Dockerfile."
        )

    parts: list[str] = []
    if result.stdout:
        parts.append(f"STDOUT:\n{result.stdout.rstrip()}")
    if result.stderr:
        parts.append(f"STDERR:\n{result.stderr.rstrip()}")
    if not parts:
        parts.append(f"(no output, exit code {result.returncode})")
    elif result.returncode != 0:
        parts.append(f"(exit code: {result.returncode})")

    output = "\n\n".join(parts)
    if len(output) > READ_FILE_MAX_CHARS:
        output = (
            output[:READ_FILE_MAX_CHARS]
            + f"\n[...{len(output) - READ_FILE_MAX_CHARS} chars truncated]"
        )
    return output


def web_search(query: str) -> str:
    """Search the web. Returns up to 5 results with titles + URLs + snippets.

    Provider is configured via WEB_SEARCH_PROVIDER env var (default
    'tavily'). The function dispatches to the right backend.
    """
    provider = os.environ.get("WEB_SEARCH_PROVIDER", "tavily").lower()
    if provider == "tavily":
        return _search_tavily(query)
    return f"ERROR: web search provider '{provider}' not implemented"


def _search_tavily(query: str) -> str:
    """Tavily backend. Free tier at https://tavily.com (1000/month)."""
    key = os.environ.get("TAVILY_API_KEY")
    if not key:
        return (
            "ERROR: TAVILY_API_KEY not set in .env. Get a free key at "
            "https://tavily.com (1000 searches/month free)."
        )
    try:
        response = httpx.post(
            "https://api.tavily.com/search",
            json={
                "api_key": key,
                "query": query,
                "max_results": 5,
                "include_answer": True,
            },
            timeout=30.0,
        )
    except httpx.HTTPError as e:
        return f"ERROR: Tavily request failed: {e}"
    if response.status_code != 200:
        return f"ERROR: Tavily API {response.status_code}: {response.text}"
    data = response.json()

    lines: list[str] = []
    if data.get("answer"):
        lines.append(f"Answer summary: {data['answer']}\n")
    lines.append(f"Top results for '{query}':")
    for i, result in enumerate(data.get("results", []), 1):
        lines.append(f"\n[{i}] {result.get('title', '(no title)')}")
        lines.append(f"    URL: {result.get('url', '')}")
        snippet = (result.get("content") or "").strip()
        if snippet:
            # Cap each snippet so a single chatty result doesn't dominate.
            if len(snippet) > 500:
                snippet = snippet[:500] + "..."
            lines.append(f"    {snippet}")
    return "\n".join(lines)


def web_fetch(url: str) -> str:
    """Fetch a URL and return its main text content (HTML boilerplate stripped).

    Uses BeautifulSoup to drop scripts/styles/nav/footer/aside, then
    collapses whitespace. Caps output at READ_FILE_MAX_CHARS (16K) — the
    LLM rarely needs more, and bigger blobs blow up the context window.
    """
    try:
        response = httpx.get(
            url,
            timeout=30.0,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Homunculus AI assistant)"},
        )
    except httpx.HTTPError as e:
        return f"ERROR: fetch failed: {e}"
    if response.status_code != 200:
        return f"ERROR: HTTP {response.status_code} fetching {url}"

    content_type = response.headers.get("content-type", "").lower()
    text = response.text

    if "html" in content_type:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(text, "lxml")
        # Drop the noise — boilerplate and embedded code.
        for tag in soup(["script", "style", "nav", "footer", "aside", "header", "noscript"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        # Collapse blank-line runs.
        text = "\n".join(line for line in text.splitlines() if line.strip())

    if len(text) > READ_FILE_MAX_CHARS:
        return (
            text[:READ_FILE_MAX_CHARS]
            + f"\n\n[...{len(text) - READ_FILE_MAX_CHARS} chars truncated]"
        )
    return text


def notify(text: str) -> str:
    """Push a proactive message to the user via Telegram.

    Use sparingly. This interrupts the user (notification on their phone),
    so reserve it for things genuinely worth their attention: a critical
    deadline approaching, a question that blocks further progress, etc.
    Routine summaries should go to memory/files, not notifications.

    Returns a confirmation or a configuration error.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_ALLOWED_USER_ID")
    if not token or not chat_id:
        return (
            "ERROR: Telegram is not configured (TELEGRAM_BOT_TOKEN or "
            "TELEGRAM_ALLOWED_USER_ID missing). Cannot send notification."
        )
    try:
        response = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10.0,
        )
    except httpx.HTTPError as e:
        return f"ERROR: Telegram request failed: {e}"
    if response.status_code != 200:
        return f"ERROR: Telegram API {response.status_code}: {response.text}"
    return f"Notification delivered ({len(text)} chars)."


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
            "description": "Save a durable fact to long-term memory (persists across sessions). Not for ephemeral state.",
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
                    "related": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional. Related memory slugs (filenames without .md).",
                    },
                },
                "required": ["name", "description", "type", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "python",
            "description": "Run Python in a sandbox (no network, 30s, ephemeral). Returns stdout/stderr. Pass data via the code itself.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Python source to execute. Print results to stdout.",
                    },
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web. Returns ~5 results + summary. Cite full URLs in your reply when you use results.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Fetch a URL and return its main text. Capped ~16K chars.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to fetch."},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_next_tick",
            "description": "Set heartbeat's next wake time. ISO 8601 local datetime, within 24h. Heartbeat-only.",
            "parameters": {
                "type": "object",
                "properties": {
                    "iso_datetime": {
                        "type": "string",
                        "description": "Target wake time, ISO 8601, local timezone.",
                    },
                },
                "required": ["iso_datetime"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "notify",
            "description": "Push a Telegram message. INTERRUPTS the user — use only for time-sensitive things. Routine summaries belong in files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Message text. Keep concise.",
                    },
                },
                "required": ["text"],
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
    "python": python_exec,
    "web_search": web_search,
    "web_fetch": web_fetch,
    "schedule_next_tick": schedule_next_tick,
    "notify": notify,
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
