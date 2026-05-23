"""File-system tools: read_file, write_file."""

from __future__ import annotations

from pathlib import Path

from ._helpers import READ_FILE_MAX_CHARS, normalize_workspace_path


def read_file(path: str) -> str:
    text = Path(normalize_workspace_path(path)).read_text(encoding="utf-8")
    if len(text) <= READ_FILE_MAX_CHARS:
        return text
    truncated = text[-READ_FILE_MAX_CHARS:]
    omitted = len(text) - READ_FILE_MAX_CHARS
    return f"[...{omitted} chars omitted from start; showing tail...]\n\n{truncated}"


def write_file(path: str, content: str) -> str:
    p = Path(normalize_workspace_path(path))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Wrote {len(content)} bytes to {p}"


