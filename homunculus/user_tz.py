"""User-timezone autodetection — a tiny shared module.

The user's timezone is detected from the browser on their first web visit
and persisted in workspace/user_tz.txt. Heartbeat (autonomous, no browser)
reads from the same file, so prompts, get_current_time defaults, and any
server-side date formatting all use the user's actual wall clock without
any manual config.

Falls back to system local TZ if the file doesn't exist yet (first run
before the web UI is loaded). Falls back to UTC if even that fails.
"""

from __future__ import annotations

import os
from datetime import datetime, tzinfo
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover — Python 3.9+
    ZoneInfo = None  # type: ignore[assignment]


# Workspace-relative path. The same file is visible to every service
# (web, heartbeat, telegram) via the shared workspace volume. Resolved
# per-call so tests can monkeypatch HOMUNCULUS_USER_TZ_FILE between
# cases without reloading the module.
def _tz_file() -> Path:
    return Path(os.environ.get("HOMUNCULUS_USER_TZ_FILE", "user_tz.txt"))

# Module-level cache so we don't hit the filesystem on every datetime call.
# Invalidated when the file mtime changes OR the resolved path changes
# (tests swap HOMUNCULUS_USER_TZ_FILE between cases).
_cached_name: str | None = None
_cached_mtime: float | None = None
_cached_path: str | None = None


def _read_stored() -> str | None:
    """Return the persisted TZ name, or None if no detection has happened yet."""
    global _cached_name, _cached_mtime, _cached_path
    path = _tz_file()
    if _cached_path != str(path):
        _cached_name = None
        _cached_mtime = None
        _cached_path = str(path)
    try:
        st = path.stat()
    except FileNotFoundError:
        _cached_name = None
        _cached_mtime = None
        return None
    if _cached_mtime != st.st_mtime:
        _cached_name = path.read_text(encoding="utf-8").strip() or None
        _cached_mtime = st.st_mtime
    return _cached_name


def _system_local_name() -> str:
    """Best-effort guess at the system's local TZ (used before the browser
    has set one). Returns "UTC" on Docker since the container is UTC-only."""
    try:
        local = datetime.now().astimezone().tzinfo
        if local is None:
            return "UTC"
        # zoneinfo returns ZoneInfo('America/Los_Angeles') etc; .key has the name
        return getattr(local, "key", None) or str(local) or "UTC"
    except Exception:
        return "UTC"


def get_user_tz_name() -> str:
    """Return the user's IANA TZ name, preferring stored → system → UTC."""
    return _read_stored() or _system_local_name() or "UTC"


def get_user_tz() -> tzinfo | None:
    """Return a tzinfo for the user's TZ, or None if ZoneInfo unavailable."""
    if ZoneInfo is None:
        return None
    name = get_user_tz_name()
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC") if name != "UTC" else None


def now_user_tz() -> datetime:
    """Current time in the user's TZ. Falls back to naive UTC if no zoneinfo."""
    tz = get_user_tz()
    return datetime.now(tz) if tz else datetime.now()


def now_user_naive() -> datetime:
    """Current wall-clock time in the user's TZ, returned as a naive
    datetime.

    Use this for comparisons with naive ISO strings (due_at, last_fired_at,
    etc.) stored by the task store. Mixing container-local datetime.now()
    with user-local naive strings causes tasks to fire hours late on
    containers running in UTC.

    Only honors a TZ that was *explicitly stored* (browser-detected via
    set_user_tz_name). If nothing is stored, returns datetime.now() — the
    system's actual local time. We deliberately do NOT fall through to
    `get_user_tz()` here because that path resolves abbreviated names
    like "IST" (Indian Standard Time on macOS) to ZoneInfo("IST") which
    is Europe/Dublin — silently shifting every datetime by 5.5 hours.
    The browser-fed path always sends IANA names ("Asia/Kolkata"), so
    that path is safe.
    """
    stored = _read_stored()
    if not stored or ZoneInfo is None:
        return datetime.now()
    try:
        tz = ZoneInfo(stored)
    except Exception:
        return datetime.now()
    return datetime.now(tz).replace(tzinfo=None)


def set_user_tz_name(tz_name: str) -> None:
    """Persist a new TZ. Called from the web API when the browser reports its TZ.

    Validates the name against ZoneInfo so we don't store garbage.
    """
    if ZoneInfo is None:
        return
    try:
        ZoneInfo(tz_name)
    except Exception:
        return  # ignore invalid names silently — better than crashing
    path = _tz_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tz_name + "\n", encoding="utf-8")
    # Invalidate cache so the next read picks it up immediately.
    global _cached_name, _cached_mtime
    _cached_name = None
    _cached_mtime = None
