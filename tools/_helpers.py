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


# The workspace root is the process cwd at module import — set by
# docker-compose to /app/workspace in the production containers, and
# the repo root in dev / tests. Captured once so a later os.chdir()
# can't quietly widen the sandbox.
WORKSPACE_ROOT = Path(os.environ.get("HOMUNCULUS_WORKSPACE_ROOT", os.getcwd())).resolve()


class PathOutsideWorkspace(ValueError):
    """Raised when a tool argument resolves outside the workspace sandbox."""


def normalize_workspace_path(path: str) -> str:
    """Resolve `path` against the workspace root, enforcing that the
    result stays inside the sandbox.

    Accepts relative paths (resolved against the workspace root),
    workspace-relative absolutes (`/app/workspace/...` or `workspace/...`),
    and rejects anything that would escape: `/etc/passwd`, `..` traversal,
    or symlinks pointing outside the workspace. Raises PathOutsideWorkspace
    on rejection; callers convert that to a user-visible ERROR string so
    the agent gets a clear failure to retry against.

    Reference: pathlib resolve() + is_relative_to() is the standard
    sandboxing pattern recommended in CWE-22 mitigation guides — handles
    symlinks (string prefix checks don't).
    """
    if path is None:
        raise PathOutsideWorkspace("path is required")
    raw = str(path)
    # Strip the well-known workspace prefixes so /app/workspace/memory/x
    # and memory/x resolve to the same place. Done BEFORE join so the
    # remainder is always treated as workspace-relative.
    if raw.startswith("/app/workspace/"):
        raw = raw[len("/app/workspace/"):]
    elif raw == "/app/workspace":
        raw = ""
    elif raw.startswith("workspace/"):
        raw = raw[len("workspace/"):]

    candidate = Path(raw)
    # Absolute paths that aren't under WORKSPACE_ROOT get rejected
    # outright. Resolved form follows symlinks, so a symlink in the
    # workspace pointing to /etc cannot launder access.
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        resolved = (WORKSPACE_ROOT / candidate).resolve()

    try:
        resolved.relative_to(WORKSPACE_ROOT)
    except ValueError:
        raise PathOutsideWorkspace(
            f"path {path!r} resolves outside the workspace sandbox"
        )

    # Return relative-to-cwd form so the existing tool internals (which
    # call Path(...).read_text() etc.) keep working unchanged. Using a
    # relative path also keeps log lines compact.
    try:
        return str(resolved.relative_to(WORKSPACE_ROOT))
    except ValueError:
        # Belt-and-braces — the check above already guarantees this
        # succeeds; included so a future refactor of WORKSPACE_ROOT
        # can't silently drop sandboxing.
        raise PathOutsideWorkspace(
            f"path {path!r} resolved to {resolved} which is outside the workspace"
        )


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
