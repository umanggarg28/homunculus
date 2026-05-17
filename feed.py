"""
Homunculus web UI (Phase 6.3).

A single FastAPI app serving four pages plus an SSE event stream:

    /            chat with the agent (streaming responses)
    /feed        live thinking feed (the old / from Phase 6.2)
    /memory      browse typed memory entries
    /logs        browse daily conversation logs
    /status      JSON: per-service liveness (last event age)
    /chat/send   POST → streaming response, tokens via SSE
    /events      SSE stream tailing _events.jsonl

All pages share a header nav with the live status panel. No build
step — HTML/CSS/JS inline. Same "no framework" ethos as the rest of
the project.

The chat reuses the same _session.json that the REPL and Telegram bot
use, so you can start a thought on the web, continue on your phone.
Concurrent edits are not protected — realistically you're not on two
surfaces at once.
"""

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

import tools
from core import Agent
from memory import Memory


# --- Config ---------------------------------------------------------------

EVENTS_PATH = Path(os.environ.get("HOMUNCULUS_EVENTS_PATH", "_events.jsonl"))
MEMORY_DIR = Path(os.environ.get("HOMUNCULUS_MEMORY_DIR", "./memory"))

POLL_INTERVAL = 0.25
INITIAL_TAIL = 50

# "Idle" / "stale" thresholds for the status panel. Heartbeat ticks
# default to 10-min intervals, so anything quieter than ~12 min counts
# as idle. Telegram bot can sit silent for hours waiting for messages —
# it gets a more generous threshold.
STATUS_IDLE_SECONDS = 12 * 60
STATUS_STALE_SECONDS = 60 * 60


app = FastAPI(title="Homunculus Web UI")

# Shared agent for /chat. Lazy-init so we don't open Memory() at import
# time (the feed service runs even when MEMORY_DIR is empty).
_chat_memory: Memory | None = None
_chat_agent: Agent | None = None


def _get_chat_agent() -> Agent:
    """Lazy singleton — the chat agent is built on first /chat/send."""
    global _chat_agent, _chat_memory
    if _chat_agent is None:
        _chat_memory = Memory(MEMORY_DIR)
        tools.init(_chat_memory, autonomous=False)
        _chat_agent = Agent(memory=_chat_memory)
        _chat_agent.restore_session()
    return _chat_agent


# --- Pages ---------------------------------------------------------------

@app.get("/")
def page_chat() -> HTMLResponse:
    return HTMLResponse(_render_page("Chat", _CHAT_BODY, active="chat"))


@app.get("/feed")
def page_feed() -> HTMLResponse:
    return HTMLResponse(_render_page("Feed", _FEED_BODY, active="feed"))


@app.get("/memory")
def page_memory() -> HTMLResponse:
    entries = _list_memory_entries()
    rows_html = "".join(
        f'<li><a href="/memory/{e["filename"]}">{e["name"]}</a>'
        f'<span class="badge badge-{e["type"]}">{e["type"]}</span>'
        f'<span class="muted">{e["description"]}</span></li>'
        for e in entries
    ) or '<li class="muted">No memories yet.</li>'
    body = (
        f'<h2>Memory</h2>'
        f'<p class="muted">{len(entries)} entries. Click one to view its body.</p>'
        f'<ul class="memory-list">{rows_html}</ul>'
    )
    return HTMLResponse(_render_page("Memory", body, active="memory"))


@app.get("/memory/{filename}")
def page_memory_entry(filename: str) -> HTMLResponse:
    safe = _safe_subpath(filename, MEMORY_DIR)
    if safe is None or not safe.exists() or not safe.is_file():
        raise HTTPException(404, "Memory entry not found")
    text = safe.read_text(encoding="utf-8")
    body = (
        f'<h2>{filename}</h2>'
        f'<p><a href="/memory">&larr; back to memory</a></p>'
        f'<pre class="file-body">{_html_escape(text)}</pre>'
    )
    return HTMLResponse(_render_page(filename, body, active="memory"))


@app.get("/logs")
def page_logs() -> HTMLResponse:
    log_files = _list_log_files()
    rows_html = "".join(
        f'<li><a href="/logs/{lf["rel"]}">{lf["date"]}</a>'
        f'<span class="muted">{lf["size_kb"]} KB</span></li>'
        for lf in log_files
    ) or '<li class="muted">No logs yet.</li>'
    body = (
        f'<h2>Daily Logs</h2>'
        f'<p class="muted">{len(log_files)} log files. Newest first.</p>'
        f'<ul class="memory-list">{rows_html}</ul>'
    )
    return HTMLResponse(_render_page("Logs", body, active="logs"))


@app.get("/logs/{rel:path}")
def page_log_entry(rel: str) -> HTMLResponse:
    logs_root = MEMORY_DIR / "logs"
    safe = _safe_subpath(rel, logs_root)
    if safe is None or not safe.exists() or not safe.is_file():
        raise HTTPException(404, "Log not found")
    text = safe.read_text(encoding="utf-8")
    body = (
        f'<h2>{rel}</h2>'
        f'<p><a href="/logs">&larr; back to logs</a></p>'
        f'<pre class="file-body">{_html_escape(text)}</pre>'
    )
    return HTMLResponse(_render_page(rel, body, active="logs"))


# --- Data endpoints ------------------------------------------------------

@app.get("/status")
def status() -> JSONResponse:
    """Per-service health based on the freshness of their last event."""
    services = ["repl", "heartbeat", "telegram", "feed"]
    last_seen: dict[str, float | None] = {s: None for s in services}
    if EVENTS_PATH.exists():
        # Read up to the last ~2000 lines to find recent activity per service.
        # Bounded so this stays cheap even with a long-running events log.
        with EVENTS_PATH.open("r", encoding="utf-8") as f:
            tail = f.readlines()[-2000:]
        for line in tail:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            svc = rec.get("service")
            ts = rec.get("ts")
            if svc not in last_seen or not ts:
                continue
            try:
                t = datetime.fromisoformat(ts).timestamp()
            except ValueError:
                continue
            if last_seen[svc] is None or t > last_seen[svc]:
                last_seen[svc] = t

    now = datetime.now().timestamp()
    result = {}
    for svc, t in last_seen.items():
        if t is None:
            result[svc] = {"state": "unknown", "last_seen": None, "age_s": None}
            continue
        age = int(now - t)
        if age < STATUS_IDLE_SECONDS:
            state = "live"
        elif age < STATUS_STALE_SECONDS:
            state = "idle"
        else:
            state = "stale"
        result[svc] = {"state": state, "last_seen": int(t), "age_s": age}
    return JSONResponse(result)


@app.post("/chat/send")
async def chat_send(request: Request):
    """Stream the agent's reply to a posted user message.

    Body: JSON {"message": "..."}
    Response: text/event-stream with `data: <chunk>\\n\\n` lines plus a
    final `event: done` line so the client can close the connection.
    """
    payload = await request.json()
    user_message = (payload or {}).get("message", "").strip()
    if not user_message:
        raise HTTPException(400, "missing 'message'")

    agent = _get_chat_agent()

    def gen():
        try:
            for chunk in agent.chat_stream(user_message):
                # SSE encoding: split chunks on newlines because SSE
                # treats lines starting with `data:` as continuation.
                for line in chunk.splitlines() or [""]:
                    yield f"data: {line}\n"
                yield "\n"
        except Exception as e:
            yield f"data: [error: {type(e).__name__}: {e}]\n\n"
        finally:
            if _chat_memory is not None:
                _chat_memory.save_session(agent.history)
            yield "event: done\ndata: end\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/events")
async def events_endpoint():
    """SSE stream of every event from workspace/_events.jsonl."""
    return StreamingResponse(
        _tail_events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --- Internals -----------------------------------------------------------

async def _tail_events():
    EVENTS_PATH.touch(exist_ok=True)
    with EVENTS_PATH.open("r", encoding="utf-8") as f:
        existing = f.readlines()
        for line in existing[-INITIAL_TAIL:]:
            yield _format_sse(line)
        f.seek(0, os.SEEK_END)
        while True:
            line = f.readline()
            if not line:
                await asyncio.sleep(POLL_INTERVAL)
                continue
            yield _format_sse(line)


def _format_sse(json_line: str) -> str:
    payload = json_line.rstrip("\n")
    if not payload:
        return ""
    return f"data: {payload}\n\n"


def _list_memory_entries() -> list[dict]:
    """Parse memory/*.md frontmatter (loosely) and return entries newest-first."""
    if not MEMORY_DIR.exists():
        return []
    entries = []
    for path in MEMORY_DIR.glob("*.md"):
        # Skip MEMORY.md and README.md — these aren't typed entries.
        if path.name in {"MEMORY.md", "README.md"}:
            continue
        meta = _parse_frontmatter(path)
        entries.append({
            "filename": path.name,
            "name": meta.get("name", path.stem),
            "description": meta.get("description", ""),
            "type": meta.get("type", "unknown"),
            "mtime": path.stat().st_mtime,
        })
    entries.sort(key=lambda e: e["mtime"], reverse=True)
    return entries


def _parse_frontmatter(path: Path) -> dict:
    """Loose YAML-frontmatter parser. Returns {name, description, type, ...}."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}
    block = text[4:end]
    meta = {}
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
    out = []
    for path in logs_root.rglob("*.md"):
        rel = path.relative_to(logs_root).as_posix()
        out.append({
            "rel": rel,
            "date": path.stem,
            "size_kb": round(path.stat().st_size / 1024, 1),
            "mtime": path.stat().st_mtime,
        })
    out.sort(key=lambda e: e["mtime"], reverse=True)
    return out


def _safe_subpath(rel: str, root: Path) -> Path | None:
    """Resolve a user-supplied relative path against root, reject escapes."""
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


def _html_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
    )


# --- HTML --------------------------------------------------------------

def _render_page(title: str, body_html: str, active: str) -> str:
    nav = (
        ('<a href="/" class="{}">Chat</a>' .format("active" if active == "chat" else "")) +
        ('<a href="/feed" class="{}">Feed</a>'.format("active" if active == "feed" else "")) +
        ('<a href="/memory" class="{}">Memory</a>'.format("active" if active == "memory" else "")) +
        ('<a href="/logs" class="{}">Logs</a>'.format("active" if active == "logs" else ""))
    )
    return _PAGE_SHELL.replace("{{TITLE}}", title).replace("{{NAV}}", nav).replace("{{BODY}}", body_html)


_PAGE_SHELL = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Homunculus · {{TITLE}}</title>
<style>
  :root {
    --bg: #0e0e10; --fg: #e6e6e6; --muted: #888; --border: #222;
    --accent: #6fcf97; --tool: #f2c94c; --result: #56ccf2; --user: #bb86fc;
    --reply: #6fcf97; --idle: #999; --stale: #e57373;
  }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
         background: var(--bg); color: var(--fg); font-size: 13px; }
  a { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }
  header { padding: 10px 20px; border-bottom: 1px solid var(--border);
           display: flex; justify-content: space-between; align-items: center;
           gap: 20px; flex-wrap: wrap; }
  header h1 { font-size: 14px; margin: 0; font-weight: 500; }
  nav a { color: var(--muted); margin-right: 14px; padding: 4px 8px; border-radius: 4px; }
  nav a.active { color: var(--fg); background: #1a1a1c; }
  #status-bar { display: flex; gap: 12px; font-size: 11px; }
  .svc-pill { display: inline-flex; align-items: center; gap: 5px; color: var(--muted); }
  .svc-pill::before { content: "●"; }
  .svc-pill.live::before { color: var(--accent); }
  .svc-pill.idle::before { color: var(--idle); }
  .svc-pill.stale::before { color: var(--stale); }
  .svc-pill.unknown::before { color: #444; }
  main { padding: 20px; max-width: 1100px; margin: 0 auto; }
  .muted { color: var(--muted); }
  h2 { font-size: 15px; font-weight: 600; margin: 0 0 12px; }
  pre.file-body { background: #161618; border: 1px solid var(--border); padding: 14px;
                  border-radius: 6px; white-space: pre-wrap; word-break: break-word; }
  .memory-list { list-style: none; padding: 0; }
  .memory-list li { padding: 6px 0; border-bottom: 1px solid var(--border);
                    display: grid; grid-template-columns: 1fr auto 2fr; gap: 12px; align-items: center; }
  .badge { font-size: 10px; padding: 2px 6px; border-radius: 3px; background: #2a2a2c; color: var(--muted); }
  .badge-user { color: var(--user); }
  .badge-feedback { color: var(--tool); }
  .badge-project { color: var(--result); }
  .badge-reference { color: var(--accent); }
  /* Feed/chat shared row styles */
  .row { padding: 4px 0; border-bottom: 1px solid #1a1a1a;
         display: grid; grid-template-columns: 70px 90px 1fr; gap: 12px; align-items: baseline; }
  .ts { color: var(--muted); }
  .svc { color: var(--muted); }
  .body .kind { display: inline-block; min-width: 90px; }
  .kind-user_message     .kind { color: var(--user); }
  .kind-assistant_reply  .kind { color: var(--reply); }
  .kind-tool_call        .kind { color: var(--tool); }
  .kind-tool_result      .kind { color: var(--result); }
  .text { color: var(--fg); white-space: pre-wrap; word-break: break-word; }
  /* Chat */
  #chat-log { display: flex; flex-direction: column; gap: 14px; padding-bottom: 80px; }
  .msg { padding: 10px 14px; border-radius: 8px; max-width: 80%; white-space: pre-wrap;
         word-break: break-word; line-height: 1.55; }
  .msg.user { background: #1f1a2e; align-self: flex-end; border: 1px solid #2d2540; }
  .msg.assistant { background: #161618; align-self: flex-start; border: 1px solid var(--border); }
  .msg .role { font-size: 10px; color: var(--muted); margin-bottom: 4px; text-transform: uppercase; letter-spacing: 0.5px; }
  #chat-input-bar { position: fixed; bottom: 0; left: 0; right: 0; padding: 12px 20px;
                    background: var(--bg); border-top: 1px solid var(--border); display: flex; gap: 10px; }
  #chat-input { flex: 1; padding: 10px 14px; background: #161618; color: var(--fg);
                border: 1px solid var(--border); border-radius: 6px;
                font-family: inherit; font-size: 13px; resize: none; }
  #chat-input:focus { outline: none; border-color: var(--accent); }
  #send-btn { padding: 0 18px; background: var(--accent); color: #0e0e10; border: none;
              border-radius: 6px; cursor: pointer; font-family: inherit; font-weight: 600; }
  #send-btn:disabled { opacity: 0.4; cursor: not-allowed; }
</style>
</head>
<body>
<header>
  <h1>Homunculus</h1>
  <nav>{{NAV}}</nav>
  <div id="status-bar">
    <span class="svc-pill unknown" data-svc="repl">repl</span>
    <span class="svc-pill unknown" data-svc="heartbeat">heartbeat</span>
    <span class="svc-pill unknown" data-svc="telegram">telegram</span>
    <span class="svc-pill unknown" data-svc="feed">feed</span>
  </div>
</header>
<main>{{BODY}}</main>
<script>
async function refreshStatus() {
  try {
    const r = await fetch('/status');
    const data = await r.json();
    for (const [svc, info] of Object.entries(data)) {
      const pill = document.querySelector(`.svc-pill[data-svc="${svc}"]`);
      if (!pill) continue;
      pill.className = 'svc-pill ' + info.state;
      const ageLabel = info.age_s === null ? '?' :
        info.age_s < 60 ? info.age_s + 's' :
        info.age_s < 3600 ? Math.floor(info.age_s / 60) + 'm' :
        Math.floor(info.age_s / 3600) + 'h';
      pill.title = svc + ': ' + info.state + ' (last seen ' + ageLabel + ' ago)';
    }
  } catch {}
}
refreshStatus();
setInterval(refreshStatus, 10000);
</script>
</body>
</html>
"""


_FEED_BODY = """<h2>Live Thinking Feed</h2>
<p class="muted">Every tool call, reply, and tick across all services, live.</p>
<div id="feed"></div>
<script>
const feed = document.getElementById('feed');
const src = new EventSource('/events');
function escape(s) {
  return String(s).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
}
src.onmessage = (e) => {
  let evt;
  try { evt = JSON.parse(e.data); } catch { return; }
  const row = document.createElement('div');
  row.className = 'row kind-' + evt.event;
  const time = (evt.ts || '').slice(11, 19);
  let body = '';
  if (evt.event === 'user_message') {
    body = `<span class="kind">user →</span> <span class="text">${escape(evt.text || '')}</span>`;
  } else if (evt.event === 'assistant_reply') {
    body = `<span class="kind">← reply</span> <span class="text">${escape(evt.text || '')}</span>`;
  } else if (evt.event === 'tool_call') {
    body = `<span class="kind">↳ ${escape(evt.name || '')}</span> <span class="text muted">${escape(evt.args || '')}</span>`;
  } else if (evt.event === 'tool_result') {
    body = `<span class="kind">↩ ${escape(evt.name || '')}</span> <span class="text">${escape(evt.result || '')}</span>`;
  } else {
    body = `<span class="kind">${escape(evt.event)}</span> <span class="text">${escape(JSON.stringify(evt))}</span>`;
  }
  row.innerHTML = `<span class="ts">${time}</span><span class="svc">${escape(evt.service || '')}</span><span class="body">${body}</span>`;
  feed.appendChild(row);
  const nearBottom = window.innerHeight + window.scrollY > document.body.offsetHeight - 100;
  if (nearBottom) window.scrollTo(0, document.body.scrollHeight);
};
</script>
"""


_CHAT_BODY = """<div id="chat-log"></div>
<div id="chat-input-bar">
  <textarea id="chat-input" rows="1" placeholder="Message Homunculus… (Enter to send, Shift+Enter for newline)"></textarea>
  <button id="send-btn">Send</button>
</div>
<script>
const log = document.getElementById('chat-log');
const input = document.getElementById('chat-input');
const btn = document.getElementById('send-btn');

function appendMsg(role) {
  const wrap = document.createElement('div');
  wrap.className = 'msg ' + role;
  const r = document.createElement('div');
  r.className = 'role';
  r.textContent = role;
  const c = document.createElement('div');
  c.className = 'content';
  wrap.appendChild(r);
  wrap.appendChild(c);
  log.appendChild(wrap);
  return c;
}

async function send() {
  const msg = input.value.trim();
  if (!msg) return;
  input.value = '';
  btn.disabled = true;

  const userMsg = appendMsg('user');
  userMsg.textContent = msg;
  const replyMsg = appendMsg('assistant');
  replyMsg.textContent = '…';
  window.scrollTo(0, document.body.scrollHeight);

  let first = true;
  try {
    const r = await fetch('/chat/send', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: msg}),
    });
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, {stream: true});
      // SSE events are separated by blank lines
      let parts = buffer.split('\\n\\n');
      buffer = parts.pop();
      for (const evt of parts) {
        const lines = evt.split('\\n');
        let data = '';
        let isDone = false;
        for (const line of lines) {
          if (line.startsWith('data: ')) data += line.slice(6) + '\\n';
          else if (line.startsWith('event: done')) isDone = true;
        }
        data = data.replace(/\\n$/, '');
        if (isDone) continue;
        if (first) { replyMsg.textContent = ''; first = false; }
        replyMsg.textContent += data;
        const nearBottom = window.innerHeight + window.scrollY > document.body.offsetHeight - 100;
        if (nearBottom) window.scrollTo(0, document.body.scrollHeight);
      }
    }
  } catch (e) {
    replyMsg.textContent += '\\n[error: ' + e.message + ']';
  }
  btn.disabled = false;
  input.focus();
}

btn.addEventListener('click', send);
input.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    send();
  }
});
input.focus();
</script>
"""
