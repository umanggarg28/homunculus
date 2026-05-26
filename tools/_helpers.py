"""Cross-tool helpers: path normalization, disk cache for network tools."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path


# How much of a file to return at most. Above this, we keep the TAIL
# (most recent content) and prepend a note. Keeps daily logs from
# blowing up the context window — each heartbeat tick reads the log,
# writes more to it, etc.
READ_FILE_MAX_CHARS = 16_000

# Small disk cache for network tools. Saves quota and repeated fetches
# during agent loops. Stored under workspace/cache/ in Docker.
CACHE_DIR = Path(os.environ.get("HOMUNCULUS_CACHE_DIR", "./cache"))
WEB_SEARCH_CACHE_SECONDS = int(os.environ.get("WEB_SEARCH_CACHE_SECONDS", str(3600)))  # 1h — real-time queries must be fresh
WEB_FETCH_CACHE_SECONDS = int(os.environ.get("WEB_FETCH_CACHE_SECONDS", str(6 * 3600)))  # 6h — pages change less often


def normalize_workspace_path(path: str) -> str:
    """Strip 'workspace/' or '/app/workspace/' prefixes so paths land
    correctly inside the container's cwd."""
    if path.startswith("/app/workspace/"):
        return path[len("/app/workspace/"):]
    if path.startswith("workspace/"):
        return path[len("workspace/"):]
    return path


def cache_key(kind: str, value: str) -> Path:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return CACHE_DIR / kind / f"{digest}.json"


def cache_get(kind: str, value: str, ttl_seconds: int) -> str | None:
    path = cache_key(kind, value)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    age = time.time() - float(data.get("created_at", 0))
    if age > ttl_seconds:
        return None
    result = data.get("result")
    return result if isinstance(result, str) else None


def cache_set(kind: str, value: str, result: str) -> None:
    path = cache_key(kind, value)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "created_at": time.time(),
        "key": value,
        "result": result,
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
