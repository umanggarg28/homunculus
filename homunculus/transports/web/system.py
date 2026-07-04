"""System/diagnostics routes — config, model-in-use, and service liveness."""

import json
from datetime import datetime
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse

from homunculus.core import API_URL, MODEL
from homunculus.transports import web_api as wa

router = APIRouter()


@router.get("/api/config")
def config() -> JSONResponse:
    return JSONResponse({"auth_required": bool(wa.WEB_AUTH_TOKEN)})


@router.get("/api/model")
def model_info() -> JSONResponse:
    """Return the model that actually handled the last request.

    Scans events.jsonl for the most recent llm_call event so the UI
    shows the real provider (e.g. kimi-k2.6:free when Gemini is down)
    rather than always showing the configured primary.
    Falls back to the configured primary if no events exist yet.
    """
    last_model = MODEL
    last_host = ""
    try:
        last_host = urlparse(API_URL).netloc
    except Exception:
        last_host = API_URL

    if wa.EVENTS_PATH.exists():
        try:
            with wa.EVENTS_PATH.open("r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("event") == "llm_call" and rec.get("model"):
                    last_model = rec["model"]
                    last_host = rec.get("host", last_host)
                    break
        except OSError:
            pass

    return JSONResponse({
        "model": last_model,
        "host": last_host,
        "primary": MODEL,
        "is_fallback": last_model != MODEL,
    })


@router.get("/api/status", dependencies=[Depends(wa.require_web_auth)])
def status() -> JSONResponse:
    """Per-service liveness inferred from event-stream freshness."""
    services = ["heartbeat", "telegram", "web"]
    last_seen: dict[str, float | None] = dict.fromkeys(services)

    if wa.EVENTS_PATH.exists():
        with wa.EVENTS_PATH.open("r", encoding="utf-8") as f:
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
            prev = last_seen[svc]
            if prev is None or t > prev:
                last_seen[svc] = t

    now = datetime.now().timestamp()
    result: dict[str, dict] = {}
    for svc, t in last_seen.items():
        if t is None:
            result[svc] = {"state": "unknown", "last_seen": None, "age_s": None}
            continue
        age = int(now - t)
        if age < wa.STATUS_IDLE_SECONDS:
            state = "live"
        elif age < wa.STATUS_STALE_SECONDS:
            state = "idle"
        else:
            state = "stale"
        result[svc] = {"state": state, "last_seen": int(t), "age_s": age}
    return JSONResponse(result)


@router.get("/api/logs", dependencies=[Depends(wa.require_web_auth)])
def logs_list() -> JSONResponse:
    return JSONResponse(wa._list_log_files())


@router.get(
    "/api/logs/{rel:path}/raw",
    response_class=PlainTextResponse,
    dependencies=[Depends(wa.require_web_auth)],
)
def log_entry_raw(rel: str) -> PlainTextResponse:
    logs_root = wa.MEMORY_DIR / "logs"
    safe = wa._safe_subpath(rel, logs_root)
    if safe is None or not safe.exists() or not safe.is_file():
        raise HTTPException(404, "Log not found")
    return PlainTextResponse(safe.read_text(encoding="utf-8"))


@router.get("/api/setup/status", dependencies=[Depends(wa.require_web_auth)])
def setup_status() -> JSONResponse:
    """First-run readiness: which capabilities are actually wired up.

    Drives the Overview setup checklist — shown only while something is
    missing, gone forever once the install is complete. Every check reads
    the real source of truth (env, files, stores), never a cached flag.
    """
    import os as _os

    from homunculus.user_location import get_user_location

    telegram = bool(_os.environ.get("TELEGRAM_BOT_TOKEN"))
    try:
        location = get_user_location() is not None
    except Exception:
        location = False
    try:
        from homunculus.tools.google_auth import token_path
        google = token_path().exists()
    except Exception:
        google = False
    try:
        tasks = any(t.get("status") == "active" for t in wa._task_store().all())
    except Exception:
        tasks = False
    memory = wa.MEMORY_DIR.exists() and any(
        f.name not in ("MEMORY.md", "README.md") and not f.name.startswith("_")
        for f in wa.MEMORY_DIR.glob("*.md")
    )
    return JSONResponse({
        "telegram_configured": telegram,
        "location_set": location,
        "google_connected": google,
        "tasks_exist": tasks,
        "memory_seeded": memory,
        "complete": all([telegram, location, google, tasks, memory]),
    })
