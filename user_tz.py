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
# (web, heartbeat, telegram) via the shared workspace volume.
_TZ_FILE = Path(os.environ.get("HOMUNCULUS_USER_TZ_FILE", "user_tz.txt"))

# Module-level cache so we don't hit the filesystem on every datetime call.
# Invalidated when the file mtime changes.
_cached_name: str | None = None
_cached_mtime: float | None = None


def _read_stored() -> str | None:
    """Return the persisted TZ name, or None if no detection has happened yet."""
    global _cached_name, _cached_mtime
    try:
        st = _TZ_FILE.stat()
    except FileNotFoundError:
        return None
    if _cached_mtime != st.st_mtime:
        _cached_name = _TZ_FILE.read_text(encoding="utf-8").strip() or None
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
    _TZ_FILE.parent.mkdir(parents=True, exist_ok=True)
    _TZ_FILE.write_text(tz_name + "\n", encoding="utf-8")
    # Invalidate cache so the next read picks it up immediately.
    global _cached_name, _cached_mtime
    _cached_name = None
    _cached_mtime = None
