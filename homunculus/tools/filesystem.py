"""File-system tools: read_file, write_file, append_file, list_files, search_files."""

from __future__ import annotations

import os
import re
from pathlib import Path

from homunculus.config import get_config

from ._helpers import (
    PathOutsideWorkspace,
    normalize_workspace_path,
)

_MAX_LIST_ENTRIES = 200
_MAX_SEARCH_MATCHES = 50


def _sandbox_error(path: str, exc: PathOutsideWorkspace) -> str:
    """Convert a sandbox rejection into the agent-facing ERROR string.

    The agent retries on ERROR-prefixed results, so the message must
    name the rejected path and the actionable rule: stay inside the
    workspace.
    """
    return (
        f"ERROR: path '{path}' is outside the workspace sandbox. "
        f"This tool can only read or write files under the workspace "
        f"directory. Try a path relative to the workspace root."
    )


def read_file(path: str) -> str:
    try:
        safe = normalize_workspace_path(path)
    except PathOutsideWorkspace as e:
        return _sandbox_error(path, e)
    text = Path(safe).read_text(encoding="utf-8")
    max_chars = get_config().loop.read_file_max_chars
    if len(text) <= max_chars:
        return text
    truncated = text[-max_chars:]
    omitted = len(text) - max_chars
    return f"[...{omitted} chars omitted from start; showing tail...]\n\n{truncated}"


def write_file(path: str, content: str) -> str:
    try:
        safe = normalize_workspace_path(path)
    except PathOutsideWorkspace as e:
        return _sandbox_error(path, e)
    p = Path(safe)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Wrote {len(content)} bytes to {p}"


def append_file(path: str, content: str) -> str:
    try:
        safe = normalize_workspace_path(path)
    except PathOutsideWorkspace as e:
        return _sandbox_error(path, e)
    p = Path(safe)
    p.parent.mkdir(parents=True, exist_ok=True)
    # Ensure appended content starts on its own line.
    prefix = ""
    if p.exists() and p.stat().st_size > 0:
        last_byte = p.read_bytes()[-1:]
        if last_byte and last_byte != b"\n":
            prefix = "\n"
    with p.open("a", encoding="utf-8") as f:
        f.write(prefix + content)
    return f"Appended {len(content)} bytes to {p} (total size: {p.stat().st_size} bytes)"


def list_files(path: str = ".") -> str:
    """List files and directories under path (relative to workspace cwd)."""
    try:
        safe = normalize_workspace_path(path)
    except PathOutsideWorkspace as e:
        return _sandbox_error(path, e)
    root = Path(safe)
    if not root.exists():
        return f"ERROR: path '{path}' does not exist"
    if root.is_file():
        return str(root)

    # Skip noisy dirs that don't belong to the user's workspace content
    _SKIP = {"cache", "__pycache__", ".git", "node_modules", ".venv"}

    entries: list[str] = []
    for item in sorted(root.iterdir()):
        if item.name.startswith(".") or item.name in _SKIP:
            continue
        if item.is_dir():
            entries.append(f"{item.name}/")
        else:
            size = item.stat().st_size
            entries.append(f"{item.name}  ({size:,} bytes)")
        if len(entries) >= _MAX_LIST_ENTRIES:
            entries.append(f"... ({_MAX_LIST_ENTRIES} entry limit reached)")
            break

    if not entries:
        return f"(empty directory: {root})"
    return f"{root}:\n" + "\n".join(entries)


def search_files(query: str, path: str = ".", case_sensitive: bool = False) -> str:
    """Grep for query across text files under path."""
    try:
        safe = normalize_workspace_path(path)
    except PathOutsideWorkspace as e:
        return _sandbox_error(path, e)
    root = Path(safe)
    if not root.exists():
        return f"ERROR: path '{path}' does not exist"

    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        pattern = re.compile(query, flags)
    except re.error as e:
        return f"ERROR: invalid regex '{query}': {e}"

    _SKIP_DIRS = {"cache", "__pycache__", ".git", "node_modules", ".venv"}
    _TEXT_EXTS = {".md", ".txt", ".py", ".json", ".yaml", ".yml", ".toml", ".csv", ".sh", ".js", ".ts"}

    matches: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in _SKIP_DIRS]
        for fname in sorted(filenames):
            fpath = Path(dirpath) / fname
            if fpath.suffix not in _TEXT_EXTS:
                continue
            try:
                text = fpath.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if pattern.search(line):
                    rel = fpath.relative_to(root)
                    matches.append(f"{rel}:{i}: {line.strip()[:120]}")
                    if len(matches) >= _MAX_SEARCH_MATCHES:
                        matches.append(f"... (limit {_MAX_SEARCH_MATCHES} reached)")
                        return "\n".join(matches)

    return "\n".join(matches) if matches else f"No matches for '{query}' in {root}"


