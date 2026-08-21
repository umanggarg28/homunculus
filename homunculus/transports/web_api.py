"""
Homunculus FastAPI service — JSON API + SPA static hosting.

All UI lives in /web (Vite + React + TypeScript). This module is
strictly:
  - JSON endpoints under /api/*
  - SSE stream at /events
  - Static file serving for the built SPA at /

There is intentionally no HTML, CSS, or JavaScript anywhere in this
file. UI presentation is fully decoupled from the API layer.
"""

import asyncio
import json
import logging
import os
import re
import secrets
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from homunculus import tools
from homunculus.core import Agent
from homunculus.memory import Memory, link_slug, parse_related_field
from homunculus.tasks import TaskStore
# Pricing lives in stats.py — shared with the agent's own week_in_review
# tool so both surfaces report identical per-model cost numbers.
from homunculus.stats import model_cost_cents as _model_cost_cents
from homunculus.logging_config import configure_logging

configure_logging()
log = logging.getLogger(__name__)


# --- Config ---------------------------------------------------------------

EVENTS_PATH = Path(os.environ.get("HOMUNCULUS_EVENTS_PATH", "_events.jsonl"))
MEMORY_DIR = Path(os.environ.get("HOMUNCULUS_MEMORY_DIR", "./memory"))
TASKS_DIR = Path(os.environ.get("HOMUNCULUS_TASKS_DIR", "./tasks"))
SPA_DIST_DIR = Path(os.environ.get("HOMUNCULUS_WEB_DIST", "/app/web-dist"))
WEB_AUTH_TOKEN = os.environ.get("HOMUNCULUS_WEB_AUTH_TOKEN", "").strip()
# Dedicated token for the iOS Shortcut / quick-capture endpoint. Kept
# separate from the web auth token so a leaked Shortcut config can't
# also open the full dashboard. If unset, the endpoint refuses.
QUICK_CAPTURE_TOKEN = os.environ.get("HOMUNCULUS_QUICK_CAPTURE_TOKEN", "").strip()

POLL_INTERVAL = 0.25
# Minimum number of "real" (non-infra-ping) events that the SSE initial
# tail should contain, so the browser always has recent agent activity
# to show when it first connects. The tail scans back from EOF until it
# has this many real events, capped at INITIAL_TAIL_MAX raw lines.
INITIAL_TAIL_MIN_REAL = 30
INITIAL_TAIL_MAX = 2000
# Events that count as "infra noise" (matches the UI's SYSTEM_EVENTS
# filter). When the tail is dominated by these, we keep scanning back
# until the user gets actual activity (turns, tool calls, replies).
SSE_INFRA_EVENTS = {
    "service_ping", "provider_cooled", "context_compacted",
    "budget_blocked", "agent_controls_updated",
}

# Per-service liveness thresholds for /api/status.
STATUS_IDLE_SECONDS = 12 * 60
STATUS_STALE_SECONDS = 60 * 60


async def _web_ping_loop() -> None:
    """Emit a service_ping event every 10 minutes so /api/status never goes stale."""
    import homunculus.events as _ev
    while True:
        try:
            _ev.emit("service_ping", name="web", text="alive")
        except Exception:
            pass
        await asyncio.sleep(10 * 60)


@asynccontextmanager
async def _lifespan(app_: object):
    import homunculus.events as _ev
    dropped = _ev.rotate(keep_days=14)
    if dropped:
        log.info("[web] rotated _events.jsonl: dropped %d lines older than 14 days", dropped)
    task = asyncio.create_task(_web_ping_loop())
    yield
    task.cancel()


app = FastAPI(title="Homunculus API", lifespan=_lifespan)

# Memory + tools are initialised eagerly at process start so the
# /skills endpoint (and anything else that introspects tools.SCHEMAS)
# returns the real catalog before any chat has happened. The Agent
# itself is still lazy — it requires session-restoration which is
# cheaper to do on first chat call.
_chat_memory: Memory = Memory(MEMORY_DIR)
# Treat browser chat like Telegram/heartbeat: no interactive stdin,
# so shell_exec is disabled rather than hanging on a prompt.
tools.init(_chat_memory, autonomous=True)
_chat_agent: Agent | None = None
# Serialises mutations to the shared _chat_agent (history append, tool
# results, memory writes). Without this, two concurrent /api/chat/send
# streams race on the same history list and reply with corrupted /
# cross-mixed content. Held for the full duration of a chat stream —
# concurrent requests queue rather than interleave. Single-user app, so
# head-of-line blocking is acceptable; correctness > throughput.
_chat_agent_lock = threading.Lock()


def _get_chat_agent() -> Agent:
    global _chat_agent
    if _chat_agent is None:
        _chat_agent = Agent(memory=_chat_memory)
        _chat_agent.restore_session()
    return _chat_agent


def require_web_auth(
    x_homunculus_token: str = Header(default=""),
    token: str = Query(default=""),
) -> None:
    """Optional bearer-lite auth for local web/API access.

    Empty HOMUNCULUS_WEB_AUTH_TOKEN keeps localhost development frictionless.
    Once set, requests must send X-Homunculus-Token. The `token` query
    parameter exists for EventSource, which cannot set custom headers.
    """
    if not WEB_AUTH_TOKEN:
        return
    candidate = x_homunculus_token or token
    if not secrets.compare_digest(candidate, WEB_AUTH_TOKEN):
        raise HTTPException(401, "Invalid or missing Homunculus web token")


# Referenced by the memory router (chapters live under the memory vault).
CHAPTERS_DIR = MEMORY_DIR / "_chapters"


# --- API: tasks -----------------------------------------------------------

def _task_store() -> TaskStore:
    return TaskStore(TASKS_DIR)


# --- API: skill proposals (agent self-authoring, human-gated) -------------
#
# The agent files skill proposals via propose_skill; they never touch the
# live registry until approved here. Approval re-validates with the FULL
# tool catalogue (the propose-time check runs without it) and only then
# commits via Skills.save (versioned, reversible) + creates any bundled
# task. This is the human half of the containment gate on the agent's
# own behavior.

def _proposal_store():
    from homunculus.proposals import ProposalStore
    return ProposalStore(Path(os.environ.get("HOMUNCULUS_PROPOSALS_FILE", "./proposals.json")))


def _known_tool_names() -> set[str]:
    names: set[str] = set()
    for s in getattr(tools, "SCHEMAS", []) or []:
        fn = s.get("function") if isinstance(s, dict) else None
        name = (fn or {}).get("name") if fn else (s.get("name") if isinstance(s, dict) else None)
        if name:
            names.add(name)
    return names


# --- Agent run-replay builders (shared by the agent router) ---------------


def _build_agent_replay(limit: int = 12) -> list[dict]:
    """Build recent inspectable turns from the append-only event log."""
    if not EVENTS_PATH.exists():
        return []
    turns: list[dict] = []
    current: dict | None = None
    try:
        with EVENTS_PATH.open("r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-5000:]
    except OSError:
        return []

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        event = rec.get("event")
        ts = rec.get("ts")
        service = rec.get("service", "")

        if event == "user_message":
            if current is not None:
                turns.append(current)
            current = _new_replay_turn(len(turns), ts, service, rec.get("text", ""))
            continue

        if current is None:
            if _starts_autonomous_replay(event, service):
                current = _new_replay_turn(
                    len(turns),
                    ts,
                    service,
                    _autonomous_replay_label(service, event, rec),
                )
            else:
                continue
        if ts:
            current["ended_at"] = ts

        if event == "assistant_reply":
            current["assistant"] = rec.get("text", "")
            current["ended_at"] = ts
            turns.append(current)
            current = None
            continue

        if event == "llm_call":
            in_tok = int(rec.get("input_tokens") or 0)
            out_tok = int(rec.get("output_tokens") or 0)
            cached = int(rec.get("cached_tokens") or 0)
            model = rec.get("model") or rec.get("name") or ""
            cost = _model_cost_cents(model, in_tok, out_tok, cached)
            current["input_tokens"] += in_tok
            current["output_tokens"] += out_tok
            current["cached_tokens"] += cached
            current["cost_cents"] += cost
            current["models"].append({
                "ts": ts,
                "model": model,
                "host": rec.get("host", ""),
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "cached_tokens": cached,
                "cost_cents": round(cost, 4),
                "request": rec.get("request", ""),
            })
            continue

        if event in {"tool_call", "tool_result", "tool_blocked"}:
            if event == "tool_call":
                current["tools"].append({
                    "name": rec.get("name", ""),
                    "args": rec.get("args", ""),
                    "result": "",
                    "status": "pending",
                    "started_at": ts,
                })
            else:
                name = rec.get("name", "")
                target = next(
                    (t for t in reversed(current["tools"]) if t.get("name") == name and t.get("status") == "pending"),
                    None,
                )
                if target is None:
                    target = {"name": name, "args": "", "started_at": ts}
                    current["tools"].append(target)
                result = rec.get("result", "")
                target["result"] = result
                target["ended_at"] = ts
                target["status"] = (
                    "blocked" if event == "tool_blocked"
                    else "failure" if str(result).lstrip().startswith("ERROR")
                    else "success"
                )
            continue

        if event in {
            "output_guard",
            "self_correction",
            "budget_blocked",
            "provider_cooled",
            "task_failure",
            "memory_write",
            "memory_forget",
        }:
            current["guards"].append({
                "ts": ts,
                "event": event,
                "name": rec.get("name", ""),
                "text": rec.get("text", "") or rec.get("description", "") or rec.get("memory_name", ""),
                "result": rec.get("result", "") or rec.get("action", ""),
            })

    if current is not None:
        turns.append(current)

    for turn in turns:
        turn["cost_cents"] = round(float(turn.get("cost_cents") or 0.0), 4)
    return turns[-limit:][::-1]


def _new_replay_turn(index: int, ts: str | None, service: str, user: str) -> dict:
    return {
        "id": f"turn-{index + 1}",
        "started_at": ts,
        "service": service,
        "user": user,
        "assistant": "",
        "models": [],
        "tools": [],
        "guards": [],
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_tokens": 0,
        "cost_cents": 0.0,
    }


def _starts_autonomous_replay(event: str | None, service: str) -> bool:
    if service in {"", "unknown"}:
        return False
    return event in {
        "llm_call",
        "tool_call",
        "tool_result",
        "tool_blocked",
        "output_guard",
        "self_correction",
        "budget_blocked",
        "provider_cooled",
        "task_failure",
        "memory_write",
        "memory_forget",
    }


def _autonomous_replay_label(service: str, event: str | None, rec: dict) -> str:
    if service == "heartbeat":
        if event == "task_failure":
            name = rec.get("name") or "scheduled task"
            return f"Autonomous heartbeat: task failure in {name}"
        return "Autonomous heartbeat tick"
    if event in {"memory_write", "memory_forget"}:
        name = rec.get("memory_name") or rec.get("name") or "memory"
        return f"{service}: {event.replace('_', ' ')} · {name}"
    if event in {"budget_blocked", "provider_cooled"}:
        name = rec.get("name") or "provider"
        return f"{service}: {event.replace('_', ' ')} · {name}"
    return f"{service}: autonomous {event or 'event'}"


# --- Chat/SSE helpers (shared with the chat + tasks + feed routers) -------
# The handlers live in transports/web/{chat,feed}.py; these helpers stay here
# because they're either pinned by tests at this path (_visible_chat_history)
# or reused across routers (_check_chat_rate by tasks, _format_sse_data by
# chat + tasks).


# Sources whose messages are agent-internal machinery, never chat turns:
#   heartbeat  — tick prompts and mid-run model text from autonomous runs
#   refinement — skill-refinement sessions
#   harness    — corrections the loop injects (detector retries, budget
#                nudges); they're role=user but the user never typed them
_NON_CHAT_SOURCES = frozenset({"heartbeat", "refinement", "harness"})

# Harness corrections recorded before source-tagging existed carry no
# `source` field, so the set above can't catch them — they're matched
# by their fixed prefixes instead. Applies ONLY to untagged records;
# everything written since tagging is filtered by source alone.
_LEGACY_HARNESS_PREFIXES = (
    "Your last reply did not include a tool call",
    "Heads-up from the harness:",
)


def _visible_chat_history(history: list[dict]) -> list[dict]:
    """Filter persisted agent history down to visible complete chat turns.

    If an input message carries `_tx_id` (from the transcript path), the
    output entry passes it through as `_source_tx_id` so the caller can
    rewrite the entry's id to a stable transcript-based form.
    """
    messages = []
    pending_user: dict | None = None
    # Tool calls made between a user message and the final reply are
    # collected so the reply entry can carry a `tools` receipt — the
    # UI's evidence line for what the agent actually did this turn.
    pending_tools: list[str] = []
    for idx, msg in enumerate(history):
        role = msg.get("role")
        content = msg.get("content")
        if role == "assistant" and msg.get("tool_calls") and msg.get("source") not in _NON_CHAT_SOURCES:
            for tc in msg["tool_calls"]:
                name = (tc.get("function") or {}).get("name") if isinstance(tc, dict) else None
                if name:
                    pending_tools.append(name)
            # fall through: a tool-planning message is never the final
            # reply, and the checks below drop it whether or not the
            # provider attached visible-looking content.
        if not isinstance(content, str) or not content.strip():
            continue
        # Agent-internal traffic (heartbeat tick prompts, harness
        # corrections, refinement runs) shares the transcript with chat
        # but is not part of the user's conversation. It used to render
        # as fake YOU/AI bubbles — the "traces leaking into chat" bug.
        if msg.get("source") in _NON_CHAT_SOURCES:
            continue
        if (
            role == "user"
            and not msg.get("source")
            and content.startswith(_LEGACY_HARNESS_PREFIXES)
        ):
            continue
        # Skip heartbeat notifications — they live in LLM context for
        # follow-up questions but shouldn't appear as chat bubbles.
        if content.startswith("[notification I sent you at"):
            continue
        tx_id = msg.get("_tx_id")
        if role == "user":
            # A new turn starts — tool calls from an aborted or
            # tool-only exchange must not leak onto the next reply.
            pending_tools = []
            pending_user = {
                "id": f"persisted-{idx}",
                "role": role,
                "content": content,
                "source": msg.get("source", "web"),
                "ts": msg.get("ts"),
                "_raw_idx": idx,
            }
            if tx_id is not None:
                pending_user["_source_tx_id"] = tx_id
            continue
        if role == "assistant":
            # Providers may return visible-looking content together with
            # tool_calls, e.g. "Here is the table..." plus write_file().
            # That is an intermediate tool-planning message, not the final
            # user-facing reply. The final assistant message arrives after
            # the tool_result and has no tool_calls.
            if msg.get("tool_calls"):
                continue
            if pending_user is not None:
                messages.append(pending_user)
                pending_user = None
            entry = {
                "id": f"persisted-{idx}",
                "role": role,
                "content": content,
                "source": msg.get("source", "web"),
                "ts": msg.get("ts"),
                "_raw_idx": idx,
            }
            if pending_tools:
                entry["tools"] = pending_tools
                pending_tools = []
            if tx_id is not None:
                entry["_source_tx_id"] = tx_id
            # Transcript rewrite pair: _journal_append wrote the raw
            # reply, _journal_replace_last_content appended the guard-
            # rewritten form right after it. The records are adjacent
            # and the LATER one is the final form. (Also collapses the
            # legacy duplicates the unconditional rewrite left on disk.)
            prev = messages[-1] if messages else None
            if (
                prev is not None
                and prev.get("role") == "assistant"
                and prev.get("_raw_idx") == idx - 1
            ):
                # Same turn — the receipt collected for the raw form
                # belongs to the rewritten form too.
                if "tools" in prev and "tools" not in entry:
                    entry["tools"] = prev["tools"]
                messages[-1] = entry
            else:
                messages.append(entry)
    for entry in messages:
        entry.pop("_raw_idx", None)
    return messages


# Short-TTL memo for read-only derivations that scan the whole event log
# (/api/skills, /api/stats/today — seconds per call once the log is MBs).
# A fresh page load fires several of them concurrently; without this the
# scans stack up and every request on the server slows to a timeout. The
# TTL is far below the UI's poll interval, so staleness is invisible.
_memo_store: dict[str, tuple[float, Any]] = {}


def memo_ttl(key: str, ttl_s: float, compute: Callable[[], Any]) -> Any:
    now = time.monotonic()
    hit = _memo_store.get(key)
    if hit is not None and now - hit[0] < ttl_s:
        return hit[1]
    value = compute()
    _memo_store[key] = (now, value)
    return value


# Per-IP rate-limit state for /api/chat/send (in-memory, resets on restart).
_chat_rate: dict[str, list[float]] = defaultdict(list)
_CHAT_RATE_MAX = 10   # requests per window
_CHAT_RATE_WINDOW = 60  # seconds


def _check_chat_rate(request: Request) -> None:
    ip = (request.client.host if request.client else None) or "unknown"
    now = time.monotonic()
    timestamps = _chat_rate[ip]
    # Drop timestamps outside the sliding window
    _chat_rate[ip] = [t for t in timestamps if now - t < _CHAT_RATE_WINDOW]
    if len(_chat_rate[ip]) >= _CHAT_RATE_MAX:
        raise HTTPException(429, "rate limit exceeded — max 10 messages/minute")
    _chat_rate[ip].append(now)


def _format_sse_data(data: str) -> str:
    """Encode arbitrary text as JSON in one SSE data line.

    Raw SSE data lines are line-oriented. JSON preserves exact newlines,
    spaces, and code blocks, and keeps the browser parser simple.
    """
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# --- Filesystem helpers --------------------------------------------------

# Same pattern the frontend renderer resolves (MemoryContent.tsx) —
# keep the two in sync or the graph and the inline links will disagree.
_WIKILINK_RE = re.compile(r"\[\[([a-z0-9][a-z0-9\-_]*)\]\]", re.IGNORECASE)


def _list_memory_entries() -> list[dict]:
    if not MEMORY_DIR.exists():
        return []
    provenance = _memory_write_provenance()
    entries: list[dict] = []
    for path in MEMORY_DIR.glob("*.md"):
        if path.name in {"MEMORY.md", "README.md"}:
            continue
        meta = _parse_frontmatter(path)
        source = provenance.get(path.name, {})
        try:
            body = path.read_text(encoding="utf-8")
            links = _entry_links(body, meta)
        except OSError:
            links = []
        entries.append({
            "filename": path.name,
            "name": meta.get("name", path.stem),
            "description": meta.get("description", ""),
            "type": meta.get("type", "unknown"),
            "mtime": path.stat().st_mtime,
            "links": links,
            "source_service": source.get("service"),
            "source_event": source.get("event"),
            "source_action": source.get("action"),
            "source_ts": source.get("ts"),
        })
    entries.sort(key=lambda e: e["mtime"], reverse=True)
    return entries


def _entry_links(body: str, meta: dict) -> list[str]:
    """Outbound links for one memory entry, from BOTH places they are written.

    A `[[wikilink]]` in the body is the form the agent uses in prose; the
    `related:` frontmatter list is the form `remember(related=[...])` writes.
    Reading only the first halves the graph — on this vault it is 6 edges
    against 14 — and the omission is invisible, because an entry with no
    rendered links looks exactly like an entry with none written.

    Slugs are normalised so `feedback-tone` and `feedback_tone` resolve to
    one node: the two conventions are both in use across the vault.
    """
    found = {_slug(m.group(1)) for m in _WIKILINK_RE.finditer(body)}
    found.update(parse_related_field(meta.get("related")))
    return sorted(x for x in found if x)


def _slug(value: str) -> str:
    """Canonical form of a memory reference — lowercase, `-` and `_` unified."""
    return link_slug(value)


def _memory_write_provenance() -> dict[str, dict]:
    """Return the most recent memory_write event keyed by memory filename."""
    out: dict[str, dict] = {}
    if not EVENTS_PATH.exists():
        return out
    try:
        with EVENTS_PATH.open("r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-5000:]
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("event") != "memory_write":
            continue
        name = rec.get("name")
        if not name:
            continue
        out[str(name)] = {
            "ts": rec.get("ts"),
            "service": rec.get("service"),
            "event": rec.get("event"),
            "action": rec.get("action"),
        }
    return out


def _parse_frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}
    block = text[4:end]
    meta: dict[str, str] = {}
    for line in block.splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        meta[k.strip()] = v.strip()
    return meta


def _list_log_files() -> list[dict]:
    logs_root = MEMORY_DIR / "logs"
    if not logs_root.exists():
        return []
    out: list[dict] = []
    for path in logs_root.rglob("*.md"):
        out.append({
            "rel": path.relative_to(logs_root).as_posix(),
            "date": path.stem,
            "size_kb": round(path.stat().st_size / 1024, 1),
            "mtime": path.stat().st_mtime,
        })
    out.sort(key=lambda e: e["mtime"], reverse=True)
    return out


def _safe_subpath(rel: str, root: Path) -> Path | None:
    """Reject path traversal; resolve a user-supplied subpath under root."""
    try:
        candidate = (root / rel).resolve()
        root_resolved = root.resolve()
    except (OSError, ValueError):
        return None
    try:
        candidate.relative_to(root_resolved)
    except ValueError:
        return None
    return candidate


# --- Domain routers (included before the SPA catch-all so /api/* wins) ----
# Imported here, at the bottom, so `web_api` is fully defined when a router does
# `from homunculus.transports import web_api as wa` — breaks the import cycle.
from homunculus.transports.web import agent as _agent_routes  # noqa: E402
from homunculus.transports.web import capture as _capture_routes  # noqa: E402
from homunculus.transports.web import chat as _chat_routes  # noqa: E402
from homunculus.transports.web import dashboard as _dashboard_routes  # noqa: E402
from homunculus.transports.web import evals as _evals_routes  # noqa: E402
from homunculus.transports.web import feed as _feed_routes  # noqa: E402
from homunculus.transports.web import memory as _memory_routes  # noqa: E402
from homunculus.transports.web import prefs as _prefs_routes  # noqa: E402
from homunculus.transports.web import proposals as _proposals_routes  # noqa: E402
from homunculus.transports.web import skills as _skills_routes  # noqa: E402
from homunculus.transports.web import system as _system_routes  # noqa: E402
from homunculus.transports.web import tasks as _tasks_routes  # noqa: E402

app.include_router(_proposals_routes.router)
app.include_router(_prefs_routes.router)
app.include_router(_system_routes.router)
app.include_router(_memory_routes.router)
app.include_router(_agent_routes.router)
app.include_router(_skills_routes.router)
app.include_router(_tasks_routes.router)
app.include_router(_capture_routes.router)
app.include_router(_dashboard_routes.router)
app.include_router(_evals_routes.router)
app.include_router(_chat_routes.router)
app.include_router(_feed_routes.router)


# --- Static SPA hosting (mounted last so /api/* takes precedence) --------

# In production the Docker image copies the built SPA into SPA_DIST_DIR.
# If the directory is missing (e.g. running uvicorn directly during
# development without a build), we skip the mount so /api/* still works
# and the developer is expected to use `npm run dev` for the UI.
if SPA_DIST_DIR.exists():
    app.mount("/assets", StaticFiles(directory=SPA_DIST_DIR / "assets"), name="assets")

    @app.middleware("http")
    async def _spa_cache_headers(request, call_next):
        """Cache policy that survives redeploys.

        Hashed chunk files under /assets are immutable by construction
        (content-addressed names) → cache forever. Everything else the
        SPA shell serves — index.html above all — must REVALIDATE every
        time: without an explicit Cache-Control, browsers heuristically
        cache index.html, and after a redeploy a stale shell requests
        chunk files that no longer exist (404 → the route crashes).
        no-cache means "store, but ask first", so unchanged shells still
        answer 304 via the ETag.
        """
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif not path.startswith("/api/") and path != "/events":
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/{full_path:path}")
    def spa_catchall(full_path: str):
        """Serve real root-level files (manifest.json, sw.js, icons) when
        they exist in the build; otherwise the SPA index so client-side
        routing works on hard refresh. Without the file check, the PWA
        manifest and service worker came back as index.html — a syntax
        error in the manifest and an unsupported-MIME service worker on
        every page load."""
        if full_path and "/" not in full_path:
            candidate = SPA_DIST_DIR / full_path
            if candidate.is_file():
                return FileResponse(candidate)
        return FileResponse(SPA_DIST_DIR / "index.html")
