"""
Homunculus live thinking feed.

A tiny FastAPI app that tails workspace/_events.jsonl and streams
new lines to a browser via Server-Sent Events. Open
http://localhost:8000 to watch your agent think across all services
in real time.

Architecture (deliberately boring):

    services emit → workspace/_events.jsonl ← feed.py tails
                                                    ↓
                                              SSE stream
                                                    ↓
                                              browser EventSource

SSE was chosen over WebSockets because traffic is server→client only,
SSE is plain HTTP (works through every proxy), and browsers auto-
reconnect on drop. Roughly 20 lines of streaming vs ~80 for WebSocket
lifecycle.
"""

import asyncio
import json
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse


EVENTS_PATH = Path(os.environ.get("HOMUNCULUS_EVENTS_PATH", "_events.jsonl"))

# How long to wait when the file has no new lines. Lower = snappier
# updates but more wasted reads. 0.25s feels live without spinning a core.
POLL_INTERVAL = 0.25

# How many trailing lines to replay when a client first connects.
# Lets you open the page mid-session and see recent context immediately.
INITIAL_TAIL = 50


app = FastAPI(title="Homunculus Live Thinking Feed")


@app.get("/")
def index() -> HTMLResponse:
    """Serve the single-page UI. All client logic is inline below."""
    return HTMLResponse(_PAGE_HTML)


@app.get("/events")
async def events():
    """SSE stream of events. Each emitted line is one JSON-encoded record."""
    return StreamingResponse(
        _tail_events(),
        media_type="text/event-stream",
        headers={
            # Tell intermediaries not to buffer — SSE needs immediate flush.
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


async def _tail_events():
    """Async generator yielding SSE-formatted lines.

    On first connect: replay the last INITIAL_TAIL lines so the user
    sees recent context, then enter tail mode for new lines.

    Tail mode polls the file size — if it grew, read the new bytes.
    No watchdog/inotify dependency; the 0.25s loop is cheap and works
    identically across macOS/Linux.
    """
    EVENTS_PATH.touch(exist_ok=True)

    with EVENTS_PATH.open("r", encoding="utf-8") as f:
        # Replay tail of file to bootstrap the view.
        existing = f.readlines()
        for line in existing[-INITIAL_TAIL:]:
            yield _format_sse(line)
        # Seek to end and stream new lines as they appear.
        f.seek(0, os.SEEK_END)

        while True:
            line = f.readline()
            if not line:
                await asyncio.sleep(POLL_INTERVAL)
                continue
            yield _format_sse(line)


def _format_sse(json_line: str) -> str:
    """Wrap a JSONL line as one SSE data event.

    SSE wire format: `data: <payload>\\n\\n`. We pass the JSON straight
    through; the browser parses it.
    """
    payload = json_line.rstrip("\n")
    if not payload:
        return ""
    return f"data: {payload}\n\n"


_PAGE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Homunculus — Live Thinking Feed</title>
<style>
  :root {
    --bg: #0e0e10;
    --fg: #e6e6e6;
    --muted: #888;
    --accent: #6fcf97;
    --tool: #f2c94c;
    --result: #56ccf2;
    --user: #bb86fc;
    --reply: #6fcf97;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    background: var(--bg);
    color: var(--fg);
    font-size: 13px;
  }
  header {
    padding: 12px 20px;
    border-bottom: 1px solid #222;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  header h1 { font-size: 14px; margin: 0; font-weight: 500; }
  header .status { color: var(--muted); font-size: 12px; }
  header .status.live::before {
    content: "●";
    color: var(--accent);
    margin-right: 6px;
    animation: pulse 2s infinite;
  }
  @keyframes pulse { 50% { opacity: 0.3; } }
  #feed {
    padding: 16px 20px;
    line-height: 1.55;
  }
  .row {
    padding: 4px 0;
    border-bottom: 1px solid #1a1a1a;
    display: grid;
    grid-template-columns: 70px 90px 1fr;
    gap: 12px;
    align-items: baseline;
  }
  .ts { color: var(--muted); }
  .svc { color: var(--muted); }
  .body .kind { display: inline-block; min-width: 90px; }
  .kind-user_message     .kind { color: var(--user); }
  .kind-assistant_reply  .kind { color: var(--reply); }
  .kind-tool_call        .kind { color: var(--tool); }
  .kind-tool_result      .kind { color: var(--result); }
  .text { color: var(--fg); white-space: pre-wrap; word-break: break-word; }
  .muted { color: var(--muted); }
</style>
</head>
<body>
<header>
  <h1>Homunculus · Live Thinking Feed</h1>
  <div id="status" class="status">connecting…</div>
</header>
<div id="feed"></div>
<script>
const feed = document.getElementById('feed');
const status = document.getElementById('status');
const src = new EventSource('/events');

src.onopen = () => {
  status.textContent = 'live';
  status.classList.add('live');
};
src.onerror = () => {
  status.textContent = 'reconnecting…';
  status.classList.remove('live');
};

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
  // Auto-scroll to bottom unless the user has scrolled up.
  const nearBottom = window.innerHeight + window.scrollY > document.body.offsetHeight - 100;
  if (nearBottom) window.scrollTo(0, document.body.scrollHeight);
};

function escape(s) {
  return String(s)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;');
}
</script>
</body>
</html>
"""
