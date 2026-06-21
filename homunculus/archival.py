"""ArchivalMemory — Letta-style overflow store for content too large to
keep in conversation history.

Tool results that exceed the per-turn char budget land here. The agent
gets back a short token (`arch_YYYYMMDDHHMMSS_<6hex>`) and can retrieve
the full content later via semantic search.

Extracted from Memory in Bundle 2 #2 — last of the four store
extractions. Shares the same memory.db file as the markdown
embeddings index (different tables), and uses an injected `embedder`
callable so this module has no dependency on Gemini-specific code.

API
---
  insert(content, tags=[]) → token (str)
  search(query, k=5, max_chars=900) → formatted snippet string
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

# Type alias: takes text, returns a vector or None if embedding failed.
Embedder = Callable[[str], "list[float] | None"]


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length vectors.

    Duplicated from Memory rather than imported so this module has no
    upward dependency. The math is 4 lines; pulling in a shared
    embeddings.py for two callers wasn't worth the indirection.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class ArchivalMemory:
    """Sqlite-backed overflow store with semantic search."""

    def __init__(self, db_path: Path, embedder: Embedder) -> None:
        self.db_path = Path(db_path)
        self._embed = embedder

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.db_path))

    def insert(self, content: str, tags: list[str] | None = None) -> str:
        """Persist content and return its token.

        Token: `arch_YYYYMMDDHHMMSS_<6hex>` — chronologically sortable
        and grep-able in event logs.
        """
        now = time.time()
        token = (
            "arch_"
            + datetime.now().strftime("%Y%m%d%H%M%S")
            + "_"
            + secrets.token_hex(3)
        )
        tags_str = ",".join(tags or [])
        # Embed up to ~4KB; longer content is fine but won't help retrieval.
        vec = self._embed(content[:4000])
        vec_json = json.dumps(vec) if vec else None
        try:
            con = self._connect()
            con.execute(
                "INSERT INTO archival_memory (token, content, tags, vec, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (token, content, tags_str, vec_json, now),
            )
            con.commit()
            con.close()
        except Exception as e:
            return f"ERROR: archival_memory_insert failed: {e}"
        return token

    def search(self, query: str, k: int = 5, max_chars: int = 900) -> str:
        """Top-k matches by semantic similarity; per-entry content
        trimmed to `max_chars` so the agent can scan results without
        bloating its own context."""
        try:
            con = self._connect()
            rows = con.execute(
                "SELECT token, content, tags, vec, created_at FROM archival_memory"
            ).fetchall()
            con.close()
        except Exception as e:
            return f"ERROR: archival_memory_search failed: {e}"
        if not rows:
            return "(archival memory empty)"

        query_vec = self._embed(query[:2000])
        scored: list[tuple[float, str, str, str]] = []
        if query_vec:
            for token, content, tags, vec_json, _ts in rows:
                if not vec_json:
                    continue
                try:
                    vec = json.loads(vec_json)
                except Exception:
                    continue
                sim = _cosine(query_vec, vec)
                scored.append((sim, token, content, tags))
            scored.sort(reverse=True)
        if not scored:
            # Embeddings unavailable — fall back to most-recent.
            scored = [(0.0, r[0], r[1], r[2]) for r in sorted(rows, key=lambda r: -r[4])]
        top = scored[:k]
        if not top:
            return "(no matches)"
        out = []
        for sim, token, content, tags in top:
            preview = content[:max_chars]
            ellipsis = (
                f"… (+{len(content) - max_chars} chars; full content stays in token {token})"
                if len(content) > max_chars
                else ""
            )
            tag_str = f" [tags: {tags}]" if tags else ""
            sim_str = f" [sim {sim:.2f}]" if sim > 0 else ""
            out.append(f"── {token}{tag_str}{sim_str}\n{preview}{ellipsis}")
        return "\n\n".join(out)
