"""User-location config — a tiny shared module, sibling of user_tz.py.

The user's home location is captured ONCE from the browser (geolocation, or
a typed city geocoded via Open-Meteo) on the web side and persisted in
workspace/user_location.txt. The weather tool and heartbeat (autonomous, no
browser) read from the same file.

This is the deliberate design choice — location is *configured, not guessed*:
the weak model never supplies coordinates (that's the fabricated-identifier
failure mode, see the morning-brief incident). It mirrors how user_tz.py
handles the timezone, and matches how dedicated personal assistants
(weather-mcp saved locations, Home Assistant onboarded config) do it.

Storage format (one line):  ``<lat>,<lon>,<label>``
e.g. ``12.9716,77.5946,Bengaluru``

Returns None until the user has set a location — callers must handle "unset"
explicitly rather than defaulting to some guessed place.
"""

from __future__ import annotations

import os
from pathlib import Path


# Workspace-relative path, visible to every service via the shared volume.
# Resolved per-call so tests can monkeypatch the env var between cases.
def _location_file() -> Path:
    return Path(os.environ.get("HOMUNCULUS_USER_LOCATION_FILE", "user_location.txt"))


# Module-level cache, invalidated on mtime or path change (tests swap the env
# var between cases). Same shape as user_tz.py's cache.
_cached: dict[str, float | str] | None = None
_cached_mtime: float | None = None
_cached_path: str | None = None


def get_user_location() -> dict[str, float | str] | None:
    """Return the persisted location as ``{"lat": float, "lon": float,
    "label": str}``, or None if the user hasn't set one yet."""
    global _cached, _cached_mtime, _cached_path
    path = _location_file()
    if _cached_path != str(path):
        _cached = None
        _cached_mtime = None
        _cached_path = str(path)
    try:
        st = path.stat()
    except FileNotFoundError:
        _cached = None
        _cached_mtime = None
        return None
    if _cached_mtime != st.st_mtime:
        _cached = _parse(path.read_text(encoding="utf-8"))
        _cached_mtime = st.st_mtime
    return _cached


def _parse(raw: str) -> dict[str, float | str] | None:
    line = raw.strip()
    if not line:
        return None
    parts = line.split(",", 2)
    if len(parts) < 2:
        return None
    try:
        lat = float(parts[0].strip())
        lon = float(parts[1].strip())
    except ValueError:
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    label = parts[2].strip() if len(parts) == 3 else ""
    return {"lat": lat, "lon": lon, "label": label}


def set_user_location(
    lat: float | str, lon: float | str, label: str = ""
) -> dict[str, float | str] | None:
    """Persist a new home location. Called from the web API when the browser
    reports geolocation, or after geocoding a typed city. Validates ranges so
    we never store garbage; returns the stored dict or None if invalid."""
    try:
        lat = float(lat)
        lon = float(lon)
    except (TypeError, ValueError):
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    label = (label or "").replace("\n", " ").strip()[:80]
    path = _location_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Round to 4 dp (~11 m) — plenty for weather, and avoids storing a
    # needlessly precise home coordinate.
    path.write_text(f"{lat:.4f},{lon:.4f},{label}\n", encoding="utf-8")
    global _cached, _cached_mtime
    _cached = None
    _cached_mtime = None
    return get_user_location()
