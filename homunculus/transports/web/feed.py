"""Live event-stream route — the /events SSE feed that powers Traces.

Tails the shared ``_events.jsonl`` and streams every new line to the browser.
Owned config (the events path, tail bounds, poll interval, infra-noise set) and
the shared ``_format_sse_data`` encoder stay in web_api and are reached via
``wa.*``; the line-oriented SSE framing for raw event lines lives here.
"""

import asyncio
import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from homunculus.transports import web_api as wa

router = APIRouter()


@router.get("/events", dependencies=[Depends(wa.require_web_auth)])
async def events_endpoint():
    return StreamingResponse(
        _tail_events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _tail_events():
    """Stream new lines from _events.jsonl over SSE.

    Re-opens the file on every poll cycle. A long-lived handle keeps a
    Python read buffer that misses appends made by other processes
    (heartbeat/telegram/repl) writing into the same shared file.
    """
    wa.EVENTS_PATH.touch(exist_ok=True)

    # Initial tail: scan back from EOF until we've collected at least
    # INITIAL_TAIL_MIN_REAL non-infra events (so the browser always sees
    # actual agent activity, not just service_pings). Capped at
    # INITIAL_TAIL_MAX lines to bound memory on heavy infra-only periods.
    with wa.EVENTS_PATH.open("rb") as f:
        all_lines = f.readlines()
        pos = f.tell()
    take = 0
    real = 0
    for raw in reversed(all_lines):
        take += 1
        try:
            ev = json.loads(raw.decode("utf-8", errors="replace")).get("event")
            if ev not in wa.SSE_INFRA_EVENTS:
                real += 1
        except Exception:
            pass
        if real >= wa.INITIAL_TAIL_MIN_REAL or take >= wa.INITIAL_TAIL_MAX:
            break
    for raw in all_lines[-take:]:
        yield _format_sse(raw.decode("utf-8", errors="replace"))

    while True:
        try:
            size = wa.EVENTS_PATH.stat().st_size
            if size < pos:
                # File was truncated/rotated; resume from new EOF.
                pos = 0
            if size > pos:
                with wa.EVENTS_PATH.open("rb") as f:
                    f.seek(pos)
                    chunk = f.read()
                    pos += len(chunk)
                for raw in chunk.splitlines(keepends=True):
                    if raw.endswith(b"\n"):
                        yield _format_sse(raw.decode("utf-8", errors="replace"))
        except FileNotFoundError:
            pass
        await asyncio.sleep(wa.POLL_INTERVAL)


def _format_sse(json_line: str) -> str:
    payload = json_line.rstrip("\n")
    return f"data: {payload}\n\n" if payload else ""
