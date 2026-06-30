"""Built-in MCP server (FastMCP) exposing Homunculus's tools over stdio.

Runs as a standalone process: `python -m homunculus.tools.mcp_server`. The
manager (`homunculus/tools/mcp_manager.py`) launches it as a subprocess and talks to it
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
import json as _json
from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from . import (
    _meta, authoring, coach, filesystem, github,
    leetcode as leetcode_mod,
    memory_tools, news as news_mod, notify as notify_mod, report, rss, sandbox, scheduling,
    skill_refinement as skill_refinement_mod, watch, weather as weather_mod, web,
)


mcp = FastMCP("homunculus-builtin")


# ── filesystem ────────────────────────────────────────────────────────


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def read_file(
    path: Annotated[str, Field(description="Path to the file.")],
) -> str:
    """Read a UTF-8 text file from disk and return its contents."""
    return filesystem.read_file(path)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True))
def write_file(
    path: Annotated[str, Field(description="Path to write.")],
    content: Annotated[str, Field(description="Text content.")],
) -> str:
    """Write text to a file (overwrites). Creates parent dirs."""
    return filesystem.write_file(path, content)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
def append_file(
    path: Annotated[str, Field(description="Path to append to. Created if it doesn't exist.")],
    content: Annotated[str, Field(description="Text to append.")],
) -> str:
    """Append text to a file without overwriting existing content. Creates the file if needed."""
    return filesystem.append_file(path, content)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def list_files(
    path: Annotated[str, Field(description="Directory to list. Defaults to workspace root '.'.")] = ".",
) -> str:
    """List files and subdirectories at the given path in the workspace.

    Use this to discover what files exist before reading or writing.
    Skips hidden files, cache, and node_modules.
    """
    return filesystem.list_files(path)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
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


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
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
        Field(description=(
            "Related memory slugs (filenames without .md). Set this whenever "
            "the fact connects to something you already know — links become "
            "[[wikilinks]] that recall and the memory graph follow."
        )),
    ] = None,
) -> str:
    """Save a durable fact to long-term memory (persists across sessions). Not for ephemeral state."""
    return memory_tools.remember(name=name, description=description, type=type, body=body, related=related)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True))
def forget(
    name: Annotated[str, Field(description="Memory name or filename (with or without .md).")],
) -> str:
    """Delete a memory by name or filename. Use sparingly — when in doubt, leave it."""
    return memory_tools.forget(name)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
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


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def conversation_search(
    query: Annotated[
        str,
        Field(
            description=(
                "Keywords or phrase to search recent conversation logs for. "
                "Searches daily log files from the last 7 days. Use when the "
                "user asks about something you discussed recently but is no "
                "longer in the active conversation history."
            )
        ),
    ],
    days: Annotated[
        int,
        Field(description="How many days back to search (1–14). Default 7.", ge=1, le=14),
    ] = 7,
) -> str:
    """Search recent conversation logs for a keyword or phrase.

    Unlike recall() which searches the memory vault, this searches the
    raw daily conversation logs. Useful for finding what was said in a
    recent session — e.g. a specific URL, filename, or decision.
    Returns matching lines with surrounding context.
    """
    import re as _re

    from ._state import get_memory as _get_memory
    mem = _get_memory()
    if mem is None:
        return "ERROR: memory not initialised"

    log_paths = mem.recent_log_paths(days=days)
    if not log_paths:
        return f"No conversation logs found in the last {days} days."

    pattern = _re.compile(_re.escape(query), _re.IGNORECASE)
    hits: list[str] = []
    for path in log_paths:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines):
            if pattern.search(line):
                ctx_start = max(0, i - 1)
                ctx_end = min(len(lines), i + 3)
                snippet = "\n".join(lines[ctx_start:ctx_end]).strip()
                date_label = path.stem  # e.g. "2026-06-01"
                hits.append(f"[{date_label}]\n{snippet}")
                if len(hits) >= 10:
                    break
        if len(hits) >= 10:
            break

    if not hits:
        return f"No matches for '{query}' in the last {days} days of logs."
    return f"Found {len(hits)} match(es) for '{query}':\n\n" + "\n\n---\n\n".join(hits)


# ── archival memory (item 6 of robustness plan) ──────────────────────


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
def archival_memory_insert(
    content: Annotated[str, Field(description="Full text to persist to archival memory. No size limit; long content stays out of conversation.")],
    tags: Annotated[
        list[str] | None,
        Field(description="Optional free-form tags to help filter results in archival_memory_search."),
    ] = None,
) -> str:
    """Save content to archival memory and return a short reference token.

    Use when a tool result (e.g. a long web_fetch or large file) is useful
    to keep around but too big to leave in your conversation history.
    The token (arch_YYYYMMDDHHMMSS_xxxxxx) is searchable later via
    archival_memory_search. Letta/MemGPT pattern.
    """
    from ._state import get_memory as _get_memory
    mem = _get_memory()
    if mem is None:
        return "ERROR: memory not initialised"
    return mem.archival.insert(content, tags)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def archival_memory_search(
    query: Annotated[str, Field(description="Search query — semantic similarity over the archival store.")],
    k: Annotated[int, Field(description="Number of top results to return. Default 5, max 20.", ge=1, le=20)] = 5,
) -> str:
    """Search archival memory by semantic similarity.

    Returns up to k matching entries with their token, similarity score,
    tags, and a preview of the content. Use the token in your reply if
    you need to refer back to a specific entry.
    Letta/MemGPT pattern.
    """
    from ._state import get_memory as _get_memory
    mem = _get_memory()
    if mem is None:
        return "ERROR: memory not initialised"
    return mem.archival.search(query, k)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
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


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
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


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
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
        from zoneinfo import ZoneInfo
        return ZoneInfo(timezone), None
    except ImportError:
        return None, "ERROR: zoneinfo not available (requires Python 3.9+)"
    except Exception:
        return None, (
            f"Unknown timezone '{timezone}'. "
            "Use IANA names like 'Asia/Tokyo', 'America/New_York', 'Europe/London', 'UTC'."
        )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_current_time(
    timezones: Annotated[
        list[str] | None,
        Field(
            description=(
                "One or more IANA timezone names. If omitted (or empty), the user's "
                "configured timezone (HOMUNCULUS_USER_TZ, default Asia/Kolkata) is "
                "used. Pass ALL needed timezones in one call instead of calling this "
                "multiple times. "
                "Examples: ['Asia/Tokyo'], ['Asia/Tokyo','Europe/London','America/New_York']."
            )
        ),
    ] = None,
) -> str:
    """Return the current date and time for one or more IANA timezones in a single call.

    Use this for ANY time/timezone question — it is instant and accurate.
    Pass all requested timezones together so one call handles the whole query.
    Do NOT use web_search for time queries.

    Defaults to the user's configured timezone when called without arguments,
    so the agent doesn't have to remember the user's TZ — single source of truth.
    """
    if not timezones:
        # User TZ is autodetected from the browser on first visit (see
        # user_tz.py). Heartbeat and chat both read from the same file
        # so there's a single source of truth — no env var, no manual
        # config. Falls back to system local then UTC if no detection
        # has happened yet.
        try:
            from homunculus.user_tz import get_user_tz_name
            default_tz = get_user_tz_name()
        except Exception:
            default_tz = "UTC"
        timezones = [default_tz]
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


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def web_search(
    query: Annotated[str, Field(description="Search query.")],
) -> str:
    """Search the web. You CAN search the web via this tool — never tell the user you can't. Returns a DIRECT ANSWER plus supporting snippets. Cite full URLs in your reply."""
    return web.web_search(query)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def web_fetch(
    url: Annotated[str, Field(description="URL to fetch.")],
) -> str:
    """Fetch a URL and return its main text. You CAN fetch URLs via this tool — never tell the user you can't. Capped ~16K chars."""
    return web.web_fetch(url)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_weather() -> str:
    """Today's weather (condition, high/low °C) for the user's configured home location. Takes NO arguments — the location is read from config, never supplied by you. If it returns 'WEATHER UNAVAILABLE', omit weather rather than inventing a forecast."""
    return weather_mod.get_weather()


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def news_headlines(
    topic: Annotated[str, Field(description="Optional topic to prefer feeds by label, e.g. 'tech', 'ai', 'world', 'hackernews'. Empty = all sources.")] = "",
    limit: Annotated[int, Field(description="How many headlines total (1-15).")] = 5,
) -> str:
    """Top headlines from the user's configured news feeds as a ready-to-use markdown list of REAL links. Covers every source the user has listed (HN, tech, arXiv, world). Use the returned lines verbatim — never invent links. If it returns 'NEWS_UNAVAILABLE', omit the news section."""
    return news_mod.news_headlines(topic=topic, limit=limit)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def leetcode_next_problem(
    task_id: Annotated[str, Field(description="Optional task id (e.g. the daily-leetcode task). Empty = auto-detect the LeetCode task.")] = "",
) -> str:
    """The next undelivered LeetCode Top Interview 150 problem, picked deterministically from the official plan order and the task's delivery ledger. Returns its title and REAL canonical leetcode.com/problems/<slug>/ URL — use the URL verbatim. Then write the intuition, a Python solution, and complexity yourself. Returns 'LEETCODE_NEXT_UNAVAILABLE' (skip + record failure) or 'LEETCODE_ALL_DELIVERED' on edge cases."""
    return leetcode_mod.leetcode_next_problem(task_id=task_id)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True))
def web_post(
    url: Annotated[str, Field(description="URL to POST to.")],
    json_body: Annotated[
        dict | None,
        Field(description="JSON-serializable body. Sent as application/json. Use this for GraphQL queries (e.g. {\"query\": \"...\"}) and most REST APIs."),
    ] = None,
    headers: Annotated[
        dict | None,
        Field(description="Optional extra request headers (e.g. Authorization). User-Agent is set automatically."),
    ] = None,
    raw_body: Annotated[
        str | None,
        Field(description="Optional raw body string. Takes precedence over json_body — use when you need a non-JSON content type (set it via headers)."),
    ] = None,
) -> str:
    """POST to a URL and return the response. Verifies API endpoints
    (GraphQL queries, REST POSTs) that web_fetch (GET-only) can't reach
    and the python sandbox can't (sandbox is network-isolated).

    Not cached — POSTs depend on the body and are commonly mutating.
    Treat this as a side-effecting call: only use when you actually
    want the call to happen on the remote end."""
    return web.web_post(url, json_body=json_body, headers=headers, raw_body=raw_body)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
def watch_url(
    name: Annotated[str, Field(description="Stable slug identifying this watch (e.g. 'gh-stars-homunculus'). Reuse the EXACT same name on every run — the diff is against the previous call with this name.")],
    url: Annotated[str, Field(description="URL to watch. Use a URL the user gave you or one you verified via web_search — never invent or guess a URL.")],
) -> str:
    """Fetch a URL and diff it against the snapshot saved by the previous
    call with the same name. Returns FIRST SNAPSHOT, NO CHANGE, or
    CHANGED plus a unified diff of what's new.

    Use this for every recurring "tell me when something changes" task —
    never fetch twice and compare yourself. NO CHANGE means do not
    notify. On CHANGED, read the diff and judge whether it's meaningful
    (ignore timestamp/counter churn) before notifying."""
    return watch.watch_url(name, url)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def github_profile(
    user: Annotated[str, Field(description="Leave EMPTY for the operator's own profile (the configured handle) — this is the normal case. Only set it to look up a DIFFERENT person by their exact handle. NEVER guess the operator's username from their name.")] = "",
) -> str:
    """Profile-health snapshot for a public GitHub user: followers,
    public repo count, total stars/forks/open-issues, and the top repos
    by stars — diffed against the previous call so you can report
    week-over-week change ('+3 stars on homunculus'). The current
    numbers are appended after the diff. Unauthenticated public API.

    Call with NO argument for the operator's own profile."""
    return github.github_profile(user)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def rss_feed(
    name: Annotated[str, Field(description="Stable slug for this feed (e.g. 'tldr-newsletter'). Reuse it every run — new entries are detected by diffing against the previous call with this name.")],
    url: Annotated[str, Field(description="RSS or Atom feed URL. Use a feed URL the user gave you or one you verified via web_search — never guess a feed address.")],
) -> str:
    """Fetch an RSS/Atom feed and report which entries are NEW since the
    last run (diffed against the previous call with the same name).
    Returns FIRST SNAPSHOT / NO CHANGE / CHANGED + a diff. On CHANGED,
    the '+' lines are the new posts — summarise those, ignore '-' lines
    (entries that scrolled off the feed). NO CHANGE means don't notify."""
    return rss.rss_feed(name, url)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
def quiz_pick(
    topic: Annotated[
        str,
        Field(description='Leave EMPTY on the first call. If the result is mode '
              '"explore", choose a new sub-topic within the returned area, research '
              'it, then call again with topic="<your chosen sub-topic>" to register '
              'it as pending.'),
    ] = "",
) -> str:
    """Decide what to quiz this tick. Call with no argument first: you get
    either a due topic to REVIEW (already chosen for you) or an EXPLORE
    directive (nothing due → pick a fresh sub-topic within the area, research
    it, then call quiz_pick(topic=...) to commit it). The harness owns
    scheduling/rotation; you own choosing+researching the sub-topic, writing
    the question, and grading. Send the question via notify, grade the reply
    next turn with quiz_grade."""
    return coach.quiz_pick(topic)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
def quiz_grade(
    outcome: Annotated[
        Literal["correct", "partial", "wrong"],
        Field(description="How the user's answer scored against the concept."),
    ],
) -> str:
    """Grade the pending quiz question and reschedule the topic (correct →
    longer interval, wrong → ask again tomorrow). Call this after the
    user answers the question you sent with quiz_pick."""
    return coach.quiz_grade(outcome)


# ── self-authoring (propose skills for human review) ──────────────────


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
def propose_skill(
    name: Annotated[str, Field(description="Skill name, skill_<slug> (e.g. 'skill_summarize_hn').")],
    body: Annotated[str, Field(description="The COMPLETE skill markdown — '---' frontmatter (name, description, type: skill, optional states:) then the full playbook body. The body REPLACES the file, so it must be self-contained: never write 'unchanged', 'as before', or a diff. Required for a new_skill; for a skill_edit prefer `edits` instead.")] = "",
    rationale: Annotated[str, Field(description="Why this skill / edit — what failure it fixes or what job it enables.")] = "",
    kind: Annotated[
        Literal["new_skill", "skill_edit"] | None,
        Field(description="Omit to auto-detect: edit if the skill exists, else new."),
    ] = None,
    edits: Annotated[
        list[dict] | None,
        Field(description="Surgical edits for a skill_edit, as [{\"old\": \"exact text from the current skill\", \"new\": \"replacement\"}]. Each `old` must match the current skill EXACTLY and UNIQUELY (add surrounding lines if it's ambiguous); empty `new` deletes. PREFER this over `body` for targeted changes — it changes only what you specify and can't drop or corrupt the rest. Read the skill first, then copy the text to replace verbatim."),
    ] = None,
    task: Annotated[
        dict | None,
        Field(description='For a NEW skill that runs on a schedule, put the schedule HERE (never in the skill frontmatter). Shape: {"title": "...", "recurrence": "daily"|"weekly"|"none", "due_at": "YYYY-MM-DDTHH:MM:SS" (local; required only for none), "success_criteria": [{"type": "notify_called"}, {"type": "notify_contains", "text": "..."}]}. success_criteria MUST be a list of {"type": ...} objects (types: notify_called, notify_min_chars {n}, notify_contains {text}, notify_matches {pattern}, notify_has_code, notify_unique {pattern}).'),
    ] = None,
) -> str:
    """Propose a new skill, or an edit to an existing one, for HUMAN
    REVIEW. Does NOT change anything live — it files a pending proposal
    the operator approves or rejects in the dashboard. Use this to learn
    a recurring job from a request, or to fix a skill that keeps failing.
    Validation errors come straight back so you can correct and re-propose."""
    return authoring.propose_skill(name, body, rationale, kind, task, edits)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def list_proposals(
    status: Annotated[str, Field(description="pending | approved | rejected | all")] = "pending",
) -> str:
    """List review proposals and their status."""
    return authoring.list_proposals(status)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
def propose_memory_consolidation(
    limit: Annotated[int, Field(description="Maximum proposals to file, 1-20.")] = 5,
) -> str:
    """Scan memory for obvious duplicate/stale entries and file HUMAN REVIEW
    proposals. Does not change memory directly; approvals are handled in the
    dashboard. Cheap/deterministic: no extra model call."""
    return authoring.propose_memory_consolidation(limit)


# ── sandbox ───────────────────────────────────────────────────────────


@mcp.tool(name="python", annotations=ToolAnnotations(readOnlyHint=False))
def python_tool(
    code: Annotated[str, Field(description="Python source to execute. Must print results to stdout — return values are not captured.")],
) -> str:
    """Execute Python in a sandboxed subprocess (no network, 30s timeout, ephemeral filesystem).

    Use this ONLY when you need to actually run code to obtain a result (compute a value,
    parse data, test a function). Do NOT call this just to show or display code to the user —
    write code blocks directly in your reply text instead.
    """
    return sandbox.python_exec(code)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True))
def shell_exec(
    command: Annotated[str, Field(description="Shell command to run.")],
) -> str:
    """Run a shell command. User must approve each invocation."""
    return sandbox.shell_exec(command)


# ── scheduling ────────────────────────────────────────────────────────


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
def schedule_next_tick(
    iso_datetime: Annotated[str, Field(description="Target wake time, ISO 8601, local timezone.")],
) -> str:
    """Set heartbeat's next wake time. ISO 8601 local datetime, within 24h. Heartbeat-only. For recurring intent prefer create_task(recurrence=...)."""
    return scheduling.schedule_next_tick(iso_datetime)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
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
    """Create a SIMPLE reminder/notification task. You CAN schedule things — never tell the user you can't. Use recurrence='none' (default) for one-shot reminders ('remind me at 8pm'), 'daily'/'weekly' for recurring pings. Set notify=True for reminders. Dedup is automatic (same title overwrites).

    USE THIS ONLY for reminders that just notify. If the recurring job requires DOING WORK every time — fetching/searching, summarizing, delivering content, calling tools in a sequence (e.g. 'every Monday summarize HN', 'daily LeetCode problem') — do NOT use create_task. Use propose_skill(kind='new_skill', task={...}) instead: it authors a playbook (which tools, what order, message shape) so the job runs reliably. A recurring work-job created here with no skill has no procedure and fails when it fires."""
    return scheduling.create_task(title, description, due_at, recurrence, notify)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def list_tasks(
    status: Annotated[
        Literal["active", "completed", "cancelled", "all"],
        Field(description="Filter by status."),
    ] = "active",
) -> str:
    """List structured tasks by status."""
    return scheduling.list_tasks(status)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def task_health_summary() -> str:
    """Deterministic snapshot for the morning brief: today's commitments,
    real alerts (only tasks whose most recent run failed), and recovered
    tasks. Use this instead of reading raw last_runs and judging
    yourself — the latter consistently misreports old failures as
    current state."""
    return scheduling.task_health_summary()


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def week_in_review() -> str:
    """Deterministic 7-day self-report: cost vs budget (per day),
    LLM/token usage, notifications sent, task outcomes, guard blocks,
    memory churn. This JSON is the ONLY source of truth for the weekly
    report — format it into a readable message; never recount from raw
    logs or events."""
    return report.week_in_review()


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
def complete_task(
    task_id: Annotated[str, Field(description="Task id to complete.")],
    result: Annotated[str, Field(description="Optional result note.")] = "",
) -> str:
    """Mark a task complete. Recurring tasks advance to their next due time."""
    return scheduling.complete_task(task_id, result)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True))
def cancel_task(
    task_id: Annotated[str, Field(description="Task id to cancel.")],
    reason: Annotated[str, Field(description="Optional reason.")] = "",
) -> str:
    """Cancel a structured task that is no longer relevant."""
    return scheduling.cancel_task(task_id, reason)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
def record_failure(
    task_id: Annotated[str, Field(description="Task id you genuinely could not deliver.")],
    reason: Annotated[str, Field(description="One-line reason (broken source, missing data, repeated errors).")] = "",
) -> str:
    """Record a FAILED attempt when you genuinely cannot deliver a task —
    use this instead of complete_task (which the guard refuses without a
    real result). Logs the failed run, advances a recurring task to its
    next cycle, and auto-cancels after repeated consecutive failures."""
    return scheduling.record_failure(task_id, reason)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
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


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
def continue_task(
    task_id: Annotated[str, Field(description="Task id of the task you're partway through.")],
    reason: Annotated[
        str,
        Field(description="Why you're yielding (e.g. 'fetched problem, will solve next tick')."),
    ] = "",
    scratchpad_update: Annotated[
        str | None,
        Field(description="Optional short note appended to the task's scratchpad — what you've completed so the next run resumes cleanly."),
    ] = None,
) -> str:
    """Yield the current task to the next tick WITH state preserved.

    Call this when you're making real progress but won't finish *this*
    tick (provider throttling, iteration budget tightening, large
    payloads pushing context). Strictly better than running out the
    loop silently: no failure counter increment, no failure
    notification, and the scratchpad you've written survives so the
    next attempt can resume from where you stopped.

    Task is rescheduled ~10 min from now. After 3 consecutive
    continuations without a completion in between, the harness
    escalates to a real failure so the user finds out.
    """
    return scheduling.continue_task(task_id, reason, scratchpad_update)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def load_tool(
    name: Annotated[str, Field(description="Name of the tool to load (from the 'Loadable tools' list in your system prompt).")],
) -> str:
    """Load a tool's full schema into the active set for this session.

    Most tools are listed by name + one-line description in your
    system prompt under "Loadable tools" — sending their full
    JSONSchema on every call is wasteful when you don't need them.
    Call load_tool('foo') to bring 'foo' into your tool catalogue for
    the next LLM call, then invoke it normally.

    Idempotent — re-loading a tool that's already active is a no-op.
    """
    return _meta.load_tool(name)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
def task_scratchpad(
    task_id: Annotated[str, Field(description="Task id whose scratchpad to read or write.")],
    content: Annotated[
        str | None,
        Field(description="If provided, REPLACES the scratchpad with this content. Omit to read."),
    ] = None,
) -> str:
    """Read or overwrite a task's scratchpad.

    Scratchpads are per-task working state that survives across
    attempts — read at the start of each run, written as you make
    progress, automatically cleared when complete_task succeeds.
    Use this to remember what you've done so a future continuation
    doesn't redo work.

    Read: omit `content`. Overwrite: pass new `content`. To append
    instead of replace, prefer continue_task(scratchpad_update=...).
    """
    return scheduling.task_scratchpad(task_id, content)


# ── notify ────────────────────────────────────────────────────────────


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
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
    """Push a plain-text Telegram message. You CAN send Telegram messages via this tool — never tell the user you can't notify or message them. INTERRUPTS the user — use only for time-sensitive things. Routine summaries belong in files."""
    return notify_mod.notify(text, preview=preview)


# ── skill refinement ──────────────────────────────────────────────────
#
# These two tools are valid only during a skill-refinement run (see
# skill_refiner.py). Outside that context they refuse cleanly — they
# read the Skills registry off tools._state, which the runner wires
# in for the duration of the refinement Agent call.


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True))
def save_refined_skill(
    skill_name: Annotated[
        str,
        Field(description="Skill slug to overwrite, e.g. 'skill_deliver_daily_leetcode'."),
    ],
    new_body: Annotated[
        str,
        Field(description="Full markdown body of the new procedure. Must be self-contained — the next execution agent reads only this."),
    ],
    rationale: Annotated[
        str,
        Field(description="One-paragraph: what was broken in the prior body, what changed, what you verified before saving."),
    ],
) -> str:
    """Commit a redesigned skill. Archives the prior version. Only valid
    inside a refinement run.

    Once this returns success, the canonical `skill_<slug>.md` is
    replaced and the next execution agent will read the new procedure.
    Roll back via the registry's revert_to() if the redesign turns out
    to be worse.
    """
    return skill_refinement_mod.save_refined_skill(skill_name, new_body, rationale)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
def abandon_refinement(
    reason: Annotated[
        str,
        Field(description="One-paragraph: what approaches you tried and why none of them worked. Be specific so a human (or a future refinement) can use this as a starting point."),
    ],
) -> str:
    """Explicit failure marker for a refinement run. The original skill
    body stays canonical. The reason is logged for the human to inspect.

    Use this when after thorough exploration you can't find a working
    replacement procedure. Better than just running out of iterations
    silently — the reason captures what you learned."""
    return skill_refinement_mod.abandon_refinement(reason)


# ── entrypoint ────────────────────────────────────────────────────────


def main() -> None:
    # Builtin tools need workspace state (memory, autonomous flag)
    # initialised before the server starts serving requests. The
    # manager passes config via env vars.
    import os
    from pathlib import Path
    from homunculus.memory import Memory
    from homunculus.logging_config import configure_logging
    from ._state import init as init_state

    configure_logging()
    memory_dir = Path(os.environ.get("HOMUNCULUS_MEMORY_DIR", "./memory"))
    autonomous = os.environ.get("HOMUNCULUS_AUTONOMOUS", "0") == "1"
    init_state(memory=Memory(memory_dir), autonomous=autonomous, mode="build")

    mcp.run("stdio")


if __name__ == "__main__":
    main()
