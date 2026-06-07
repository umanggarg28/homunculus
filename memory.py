"""
Persistent memory for Homunculus.

The memory layout:

    memory/
        MEMORY.md           # index — one line per entry, loaded into prompt
        user_<slug>.md      # facts about the user (role, preferences, etc.)
        feedback_<slug>.md  # collaboration rules from user corrections
        project_<slug>.md   # ongoing work context (decays over time)
        reference_<slug>.md # pointers to external resources
        skill_<slug>.md     # learned procedures — "how to do X"

Each memory file has YAML-ish frontmatter:

    ---
    name: <title>
    description: <one-line summary>
    type: user|feedback|project|reference|skill
    ---

    <body>

The split is deliberate: MEMORY.md is ALWAYS loaded into the system prompt
so the agent knows what it knows. Full bodies are read on demand (via the
existing read_file tool) when the agent decides a specific memory is
relevant. This keeps context usage tiny even as memory grows.
"""

import errno
import fcntl
import json
import math
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from notifications import NotificationQueue
from stores import NextTickStore, ReflectionStore, WorldStateStore

try:
    import events as _events
except ImportError:
    _events = None  # type: ignore

ALLOWED_TYPES = {"user", "feedback", "project", "reference", "skill"}

_INDEX_HEADER = "# Memory\n\nThis index lists every durable fact I've remembered. Full bodies live in the linked files; use read_file to fetch one when relevant.\n\n"

# Schema doc dropped into memory/README.md on first init. Renders nicely
# in Obsidian (which sorts files alphabetically — README appears first).
# Explains the vault layout to any human who opens the folder and to any
# future LLM session that reads it.
_README_CONTENT = """# Homunculus Memory Vault

This folder is Homunculus's long-term memory. It's a plain folder of
markdown files — open it directly in any editor, or open it as a vault
in [Obsidian](https://obsidian.md) to get graph view, backlinks, and
mobile access via the Obsidian mobile app.

## Layout

```
memory/
  MEMORY.md              # index — every memory in one line, with [[wikilinks]]
  README.md              # this file
  user_<slug>.md         # facts about the user (role, preferences)
  feedback_<slug>.md     # collaboration rules the user has set
  project_<slug>.md      # ongoing work context
  reference_<slug>.md    # pointers to external resources
  logs/YYYY/MM/<date>.md # daily conversation logs (append-only)
```

## Memory types

| Type        | What it captures                                              |
|-------------|---------------------------------------------------------------|
| `user`      | Who the user is, role, preferences, expertise                 |
| `feedback`  | Rules the user has set ("don't do X", "always do Y")          |
| `project`   | Current work, deadlines, in-progress decisions (decays fast)  |
| `reference` | Pointers to external resources (Linear projects, dashboards)  |

Each file has YAML frontmatter (`name`, `description`, `type`, optional
`related`) followed by the memory body. The `related:` field lists
other memory slugs this entry connects to, surfaced as `[[wikilinks]]`
at the bottom of the body so Obsidian's graph picks them up.

## Using as an Obsidian vault

1. Install Obsidian (free, local-first).
2. "Open folder as vault" → point at this `memory/` directory.
3. The graph view will show how memories link via `[[wikilinks]]`.
4. For mobile: enable iCloud / Syncthing / Dropbox sync on this folder,
   then open the same folder in Obsidian mobile.

Obsidian's per-user config (`.obsidian/`) is gitignored — it's safe to
configure locally without polluting the repo.
"""


class Memory:
    """Disk-backed memory store. One instance per agent."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = root / "MEMORY.md"
        if not self.index_path.exists():
            self.index_path.write_text(_INDEX_HEADER, encoding="utf-8")
        # Schema doc — created once. Never overwritten so user edits stick.
        readme_path = root / "README.md"
        if not readme_path.exists():
            readme_path.write_text(_README_CONTENT, encoding="utf-8")
        self._init_db()
        self._migrate_vec_sidecars()
        # Sub-stores extracted from the Memory god class (Bundle 2 #2).
        # Lazy-instantiated so tests that only touch markdown memory
        # don't pay for queue setup.
        self._notifications: NotificationQueue | None = None
        self._world_state: WorldStateStore | None = None
        self._next_tick: NextTickStore | None = None
        self._reflection: ReflectionStore | None = None

    # ---- read side -----------------------------------------------------

    def load_core_block(
        self,
        max_per_type: int = 3,
        max_chars_per_entry: int = 300,
    ) -> str:
        """Return pinned always-in-context facts: user profile + key feedback rules.

        These go directly into the system prompt so the agent always knows the
        user's name, timezone, and top preferences without needing a recall()
        call. Capped tightly so the system prompt stays small even as the
        memory vault grows.

        Ordered: user_* memories first (newest-first by mtime), then
        feedback_* memories (newest-first), up to max_per_type each.
        """
        pinned: list[str] = []
        for prefix in ("user", "feedback"):
            paths = sorted(
                (p for p in self.root.glob(f"{prefix}_*.md")),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )[:max_per_type]
            for path in paths:
                text = path.read_text(encoding="utf-8")
                body = self._strip_frontmatter(text).strip()
                if not body:
                    continue
                if len(body) > max_chars_per_entry:
                    body = body[:max_chars_per_entry].rstrip() + " [...]"
                # Pull the human name from frontmatter for the label.
                name_match = re.search(r"^name:\s*(.+)$", text, re.MULTILINE)
                label = name_match.group(1).strip() if name_match else path.stem
                pinned.append(f"**{label}**: {body}")
        return "\n\n".join(pinned)

    def load_index(self, max_entries: int = 15) -> str:
        """Return MEMORY.md content with age annotations on each entry.

        We don't store ages in the file (they'd be stale instantly). Instead
        we inject "(N days ago)" at read time based on the linked file's
        actual mtime. Old memories also get a 'stale' marker so the LLM
        knows to double-check them.

        Caps at the `max_entries` most recently-touched memories (by mtime).
        Older entries stay on disk and are still discoverable via read_file —
        they just don't auto-appear in every system prompt. This bounds the
        size of the prompt as memory grows.
        """
        raw = self.index_path.read_text(encoding="utf-8")
        # Separate the header lines from the entry lines so we can sort the
        # entries by mtime without disturbing the header.
        header_lines: list[str] = []
        entry_lines: list[str] = []
        for line in raw.splitlines():
            if line.startswith("- ["):
                entry_lines.append(line)
            else:
                header_lines.append(line)

        # Sort entries newest-first by the linked file's mtime.
        entry_lines.sort(key=self._entry_mtime, reverse=True)

        # Cap and annotate.
        total = len(entry_lines)
        kept = entry_lines[:max_entries]
        annotated = [self._annotate_entry(line) for line in kept]

        footer = ""
        if total > max_entries:
            footer = (
                f"\n\n(Showing the {max_entries} most recently-touched memories "
                f"out of {total}. Older entries remain on disk — use read_file "
                f"to fetch them if you remember their filename.)"
            )

        return "\n".join(header_lines + annotated) + footer + "\n"

    def _entry_mtime(self, line: str) -> float:
        """Return the mtime of the file an index line links to, or 0."""
        match = re.match(r"^- \[[^\]]+\]\((?:\./|memory/)([^)]+)\)", line)
        if not match:
            return 0.0
        path = self.root / match.group(1)
        if not path.exists():
            return 0.0
        return path.stat().st_mtime

    def log_turn(self, role: str, content: str) -> None:
        """Append a timestamped entry to today's conversation log.

        Path layout: memory/logs/YYYY/MM/YYYY-MM-DD.md — one file per day,
        organized in a year/month tree. Append-only, never modified.
        These logs are the raw record; typed memories are the distilled
        output (created via reflect()).
        """
        now = datetime.now()
        log_dir = self.root / "logs" / f"{now.year:04d}" / f"{now.month:02d}"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"{now.year:04d}-{now.month:02d}-{now.day:02d}.md"

        timestamp = now.strftime("%H:%M:%S")
        entry = f"\n### {timestamp} — {role}\n\n{content}\n"
        with log_file.open("a", encoding="utf-8") as f:
            f.write(entry)

    # ---- session persistence -------------------------------------------
    # This is conversation history (user + assistant messages), NOT the
    # typed semantic memory above. Shared across all services that opt in
    # (REPL + Telegram), so a thread you start on your phone continues
    # in the REPL. Heartbeat deliberately does NOT use this — its ticks
    # are independent.

    @property
    def session_path(self) -> Path:
        # Underscore prefix marks this as machinery, not user-facing memory.
        # It also keeps the file out of the MEMORY.md index pattern.
        return self.root / "_session.json"

    @property
    def transcript_path(self) -> Path:
        """Path to the append-only message transcript (Letta pattern).

        Replaces the old _chat_log.jsonl band-aid. The transcript
        records every non-system message the agent produces — chat
        turns, tool calls, tool results, compaction summaries. Mid-
        session compaction rewrites the in-context pointer list inside
        the Agent (PR #111) but never deletes from this file, so the
        chat history endpoint and future heartbeat replay can always
        recover the original turns.
        """
        return self.root / "_transcript.jsonl"

    @contextmanager
    def _file_lock(self, lock_path: Path) -> Iterator[None]:
        """Exclusive fcntl flock on `lock_path` (sidecar).

        Used around read-modify-write sequences so that two writers
        (e.g., Telegram and Web both saving session) don't clobber
        each other. Blocks up to 5s before raising.
        """
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a") as f:
            for _ in range(50):
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as e:
                    if e.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
                        raise
                    time.sleep(0.1)
            else:
                raise RuntimeError(f"could not acquire {lock_path.name} after 5s")
            try:
                yield
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def save_session(self, history: list[dict]) -> None:
        """Persist the conversation history (excluding system prompt and
        transient error replies).

        The system prompt is regenerated fresh each time an agent starts
        (with current memory index, etc.), so we don't save it.

        Assistant messages whose content starts with "[error:" are
        transient failures — they should never re-enter context on the
        next turn (poisons the LLM's understanding of what happened).
        """
        body = []
        for msg in history:
            if msg.get("role") == "system":
                continue
            if (
                msg.get("role") == "assistant"
                and isinstance(msg.get("content"), str)
                and msg["content"].lstrip().startswith("[error:")
            ):
                continue
            body.append(msg)
        # Lock the session file before the atomic rename so that two
        # writers don't race between read-modify-write across services.
        with self._file_lock(self.session_path.with_suffix(".json.lock")):
            tmp = self.session_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(body), encoding="utf-8")
            tmp.replace(self.session_path)

    def load_session(self) -> list[dict]:
        """Return the previously-saved messages, or [] if no session file.

        Validates each message has a valid role and content/tool_calls
        shape. Invalid messages are dropped rather than letting them
        propagate and crash the next LLM call. This is a defense against
        partial writes (we already use atomic rename, but belt + braces)
        and against schema drift over time.
        """
        if not self.session_path.exists():
            return []
        try:
            raw = json.loads(self.session_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        if not isinstance(raw, list):
            return []
        valid_roles = {"user", "assistant", "tool", "system"}
        clean: list[dict] = []
        for msg in raw:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            if role not in valid_roles:
                continue
            # An assistant message needs content OR tool_calls; tool
            # messages need tool_call_id; user/system need content.
            if role == "assistant":
                if msg.get("content") is None and not msg.get("tool_calls"):
                    continue
            elif role == "tool":
                if not msg.get("tool_call_id"):
                    continue
            elif role in ("user", "system"):
                if msg.get("content") is None:
                    continue
            clean.append(msg)
        return clean

    def clear_session(self) -> None:
        """Delete the saved session. Called when the user types reset."""
        if self.session_path.exists():
            self.session_path.unlink()

    def clear_transcript(self) -> None:
        """Delete the persistent message transcript. Called on chapter
        close so a fresh chapter starts with an empty record."""
        if self.transcript_path.exists():
            self.transcript_path.unlink()

    # ---- pending notifications queue -----------------------------------
    #
    # The queue itself lives in notifications.NotificationQueue (extracted
    # in Bundle 2 #2). Memory exposes it as a property so existing
    # callers can do `memory.notifications.queue(text)` /
    # `memory.notifications.drain()` without holding a separate handle.

    @property
    def notifications(self) -> "NotificationQueue":
        if self._notifications is None:
            self._notifications = NotificationQueue(self.root)
        return self._notifications

    # ---- world state ---------------------------------------------------
    #
    # Owned by stores.WorldStateStore. Access via mem.world_state.read() /
    # .update() / .clear(). See stores.py for the schema and rationale.

    @property
    def world_state(self) -> WorldStateStore:
        if self._world_state is None:
            self._world_state = WorldStateStore(self.root)
        return self._world_state

    # ---- skill evaluation ----------------------------------------------
    # Skills are `skill_*.md` memories. We track `uses` and `consecutive_failures`
    # in each file's frontmatter so the daily reflection can spot skills that
    # need refinement.

    def rate_skill(self, name: str, outcome: str, notes: str = "") -> str:
        """Record a success or failure against a named skill.

        Increments `uses` and, on failure, `consecutive_failures`. Resets
        `consecutive_failures` on success. The daily reflection uses these
        counters to decide which skills need review.
        """
        if outcome not in ("success", "failure"):
            return "ERROR: outcome must be 'success' or 'failure'"
        slug = self._slugify(name)
        path = self.root / f"skill_{slug}.md"
        if not path.exists():
            candidates = [p for p in self.root.glob("skill_*.md") if slug in p.stem]
            if not candidates:
                return f"Skill '{name}' not found. Use recall() to find the exact name."
            path = candidates[0]

        text = path.read_text(encoding="utf-8")
        uses_m = re.search(r"^uses:\s*(\d+)", text, re.MULTILINE)
        failures_m = re.search(r"^consecutive_failures:\s*(\d+)", text, re.MULTILINE)
        uses = int(uses_m.group(1)) if uses_m else 0
        consec = int(failures_m.group(1)) if failures_m else 0

        uses += 1
        consec = 0 if outcome == "success" else consec + 1

        def _set_fm(t: str, key: str, value: int) -> str:
            pattern = rf"^{key}:\s*\d+$"
            if re.search(pattern, t, re.MULTILINE):
                return re.sub(pattern, f"{key}: {value}", t, flags=re.MULTILINE)
            return t.replace("---\n", f"---\n{key}: {value}\n", 1)

        text = _set_fm(text, "uses", uses)
        text = _set_fm(text, "consecutive_failures", consec)

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        note_line = f"\n- {now_str} · {outcome}" + (f" — {notes}" if notes else "")
        if "\n## Run log" in text:
            text = text.rstrip() + note_line + "\n"
        else:
            text = text.rstrip() + f"\n\n## Run log{note_line}\n"

        path.write_text(text, encoding="utf-8")
        flag = " ⚠ consider reviewing this skill" if consec >= 3 else ""
        return f"Rated '{path.stem}': {outcome} (uses={uses}, consecutive_failures={consec}){flag}"

    # ---- self-scheduled heartbeat --------------------------------------
    #
    # Owned by stores.NextTickStore. Access via mem.next_tick.set() /
    # .peek() / .pop(). The pop-on-read semantics are documented there.

    @property
    def next_tick(self) -> NextTickStore:
        if self._next_tick is None:
            self._next_tick = NextTickStore(self.root)
        return self._next_tick

    # ---- self-improvement / daily reflection ---------------------------
    #
    # Owned by stores.ReflectionStore. Access via mem.reflection.last_date()
    # / .mark(). The log-paths helper below stays on Memory because it
    # touches logs/ directly, not the reflection pointer.

    @property
    def reflection(self) -> ReflectionStore:
        if self._reflection is None:
            self._reflection = ReflectionStore(self.root)
        return self._reflection

    def recent_log_paths(self, days: int = 3) -> list[Path]:
        """Return paths to log files from the last `days` days (newest first).

        Used by reflection to know which logs are worth reviewing.
        """
        cutoff = time.time() - days * 86400
        logs_root = self.root / "logs"
        if not logs_root.exists():
            return []
        matching = [p for p in logs_root.rglob("*.md") if p.stat().st_mtime >= cutoff]
        matching.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return matching

    # ---- embedding-based search ----------------------------------------
    # Uses Gemini embedding API (free tier: 1500 RPD, 3072-dim vectors).
    # Vectors are stored in a single memory.db SQLite file (one row per
    # memory entry) instead of scattered .vec sidecar files. The markdown
    # files remain the canonical source of truth — memory.db is a pure
    # index/cache and can be rebuilt from the .md files at any time.
    # Falls back to keyword search if the Gemini key is missing or the
    # API is unavailable.

    _EMBED_MODEL = "models/gemini-embedding-2"
    _EMBED_URL = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-embedding-2:embedContent?key={key}"
    )
    _EMBED_DIM = 3072

    @property
    def _db_path(self) -> Path:
        return self.root / "memory.db"

    def _init_db(self) -> None:
        """Create memory.db and the embeddings + archival tables if they don't exist."""
        import sqlite3
        try:
            con = sqlite3.connect(str(self._db_path))
            con.execute("PRAGMA journal_mode=WAL")
            con.execute(
                "CREATE TABLE IF NOT EXISTS embeddings "
                "(filename TEXT PRIMARY KEY, vec TEXT NOT NULL)"
            )
            # Item 6 of the robustness plan — Letta-style archival memory.
            # Tool results that are too large to keep in conversation history
            # land here. Agent retrieves on demand via archival_memory_search.
            #   token       — short reference returned to the agent
            #   content     — full original text
            #   tags        — comma-separated free-form labels
            #   vec         — JSON-encoded Gemini embedding for semantic search
            #   created_at  — unix timestamp for chronological ordering
            con.execute(
                "CREATE TABLE IF NOT EXISTS archival_memory ("
                "  token TEXT PRIMARY KEY,"
                "  content TEXT NOT NULL,"
                "  tags TEXT NOT NULL DEFAULT '',"
                "  vec TEXT,"
                "  created_at REAL NOT NULL"
                ")"
            )
            con.commit()
            con.close()
        except Exception:
            pass

    def _migrate_vec_sidecars(self) -> None:
        """One-time migration: move .vec sidecar files into memory.db."""
        migrated = 0
        for vp in list(self.root.glob("*.vec")):
            try:
                vec = json.loads(vp.read_text(encoding="utf-8"))
                self._db_save_vec(vp.stem + ".md", vec)
                vp.unlink()
                migrated += 1
            except Exception:
                pass
        if migrated:
            print(f"[memory] migrated {migrated} .vec sidecars → memory.db", flush=True)

    def _db_load_vec(self, filename: str) -> list[float] | None:
        import sqlite3
        try:
            con = sqlite3.connect(str(self._db_path))
            row = con.execute(
                "SELECT vec FROM embeddings WHERE filename = ?", (filename,)
            ).fetchone()
            con.close()
            return json.loads(row[0]) if row else None
        except Exception:
            return None

    def _db_save_vec(self, filename: str, vec: list[float]) -> None:
        import sqlite3
        try:
            con = sqlite3.connect(str(self._db_path))
            con.execute("PRAGMA journal_mode=WAL")
            con.execute(
                "INSERT OR REPLACE INTO embeddings (filename, vec) VALUES (?, ?)",
                (filename, json.dumps(vec)),
            )
            con.commit()
            con.close()
        except Exception:
            pass

    def _db_delete_vec(self, filename: str) -> None:
        import sqlite3
        try:
            con = sqlite3.connect(str(self._db_path))
            con.execute("DELETE FROM embeddings WHERE filename = ?", (filename,))
            con.commit()
            con.close()
        except Exception:
            pass

    def _db_all_vecs(self) -> list[tuple[str, list[float]]]:
        """Return all (filename, vec) rows from memory.db."""
        import sqlite3
        try:
            con = sqlite3.connect(str(self._db_path))
            rows = con.execute("SELECT filename, vec FROM embeddings").fetchall()
            con.close()
            return [(r[0], json.loads(r[1])) for r in rows]
        except Exception:
            return []

    def _embed(self, text: str) -> list[float] | None:
        """Call Gemini embedding API. Returns a float vector or None on failure."""
        key = os.environ.get("HOMUNCULUS_API_KEY", "")
        if not key:
            return None
        try:
            import httpx
            resp = httpx.post(
                self._EMBED_URL.format(key=key),
                json={"model": self._EMBED_MODEL, "content": {"parts": [{"text": text[:8000]}]}},
                timeout=10.0,
            )
            if resp.status_code != 200:
                return None
            return resp.json()["embedding"]["values"]
        except Exception:
            return None

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        mag_a = math.sqrt(sum(x * x for x in a))
        mag_b = math.sqrt(sum(x * x for x in b))
        if mag_a == 0 or mag_b == 0:
            return 0.0
        return dot / (mag_a * mag_b)

    def _embed_entry(self, path: Path, text: str) -> None:
        """Compute and persist the embedding for a memory file if not cached."""
        if self._db_load_vec(path.name) is not None:
            return
        name_match = re.search(r"^name:\s*(.+)$", text, re.MULTILINE)
        desc_match = re.search(r"^description:\s*(.+)$", text, re.MULTILINE)
        body = self._strip_frontmatter(text).strip()
        embed_text = "\n".join(filter(None, [
            name_match.group(1).strip() if name_match else "",
            desc_match.group(1).strip() if desc_match else "",
            body[:2000],
        ]))
        vec = self._embed(embed_text)
        if vec:
            self._db_save_vec(path.name, vec)

    def _dedup_check(self, filename: str, vec: list[float]) -> str | None:
        """Warn if a near-duplicate memory already exists (cosine ≥ 0.92).

        Called at write time so the agent can update an existing entry
        rather than create a redundant duplicate.
        """
        candidates = [
            (self._cosine(vec, v), fn)
            for fn, v in self._db_all_vecs()
            if fn != filename
        ]
        if not candidates:
            return None
        best_sim, best_fn = max(candidates, key=lambda x: x[0])
        if best_sim >= 0.92:
            return (
                f"NOTE: a very similar memory already exists ({best_fn}, "
                f"similarity={best_sim:.2f}). Consider calling remember() with "
                f"name='{best_fn[:-3]}' to update it instead of creating a duplicate."
            )
        return None

    # ── Archival memory (Letta-style) ────────────────────────────────
    #
    # Tool results that exceed the conversation history's char budget can
    # be offloaded here. The agent gets back a short token reference and
    # can retrieve full content later via semantic search.
    #
    # API mirrors Letta:
    #   archival_memory_insert(content, tags=[]) -> token
    #   archival_memory_search(query, k=5)       -> formatted snippets

    def archival_memory_insert(self, content: str, tags: list[str] | None = None) -> str:
        """Persist a piece of content to archival memory and return its token.

        Token format: arch_YYYYMMDDHHMMSS_<6char-id> so it sorts chronologically
        and is grep-able in event logs.
        """
        import sqlite3
        import secrets
        import time as _time
        from datetime import datetime as _dt
        now = _time.time()
        token = (
            "arch_"
            + _dt.now().strftime("%Y%m%d%H%M%S")
            + "_"
            + secrets.token_hex(3)  # exactly 6 hex chars, no URL-safe escape variance
        )
        tags_str = ",".join(tags or [])
        vec = self._embed(content[:4000])  # embed up to ~4KB; longer content is fine but won't help retrieval
        vec_json = json.dumps(vec) if vec else None
        try:
            con = sqlite3.connect(str(self._db_path))
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

    def archival_memory_search(self, query: str, k: int = 5, max_chars: int = 900) -> str:
        """Retrieve top-k archival memory entries by semantic similarity.

        Falls back to chronological-most-recent if no embedding is available.
        Returned content is per-entry-trimmed to max_chars so the agent can
        scan many results without context-bloating itself.
        """
        import sqlite3
        try:
            con = sqlite3.connect(str(self._db_path))
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
                sim = self._cosine(query_vec, vec)
                scored.append((sim, token, content, tags))
            scored.sort(reverse=True)
        if not scored:
            # Fallback: most-recent first
            scored = [(0.0, r[0], r[1], r[2]) for r in sorted(rows, key=lambda r: -r[4])]
        top = scored[:k]
        if not top:
            return "(no matches)"
        out = []
        for sim, token, content, tags in top:
            preview = content[:max_chars]
            ellipsis = f"… (+{len(content) - max_chars} chars; full content stays in token {token})" if len(content) > max_chars else ""
            tag_str = f" [tags: {tags}]" if tags else ""
            sim_str = f" [sim {sim:.2f}]" if sim > 0 else ""
            out.append(f"── {token}{tag_str}{sim_str}\n{preview}{ellipsis}")
        return "\n\n".join(out)

    def search(self, query: str, limit: int = 3, max_chars: int = 900) -> str:
        """Return the most relevant memory snippets for a query.

        Uses semantic embedding similarity (Gemini) when available. Vectors
        are stored in memory.db — one row per entry, loaded in a single
        query. Falls back to keyword overlap when the API is unreachable.
        """
        # Guard: skip injection on vague follow-ups ("yes", "ok", "more").
        terms = self._query_terms(query)
        if not terms:
            return ""
        if len(terms) < 2 and not any(len(t) >= 5 for t in terms):
            return ""

        candidates: list[tuple[Path, str]] = []
        for path in self.root.glob("*.md"):
            if path.name in {"MEMORY.md", "README.md"} or path.name.startswith("_"):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                continue
            candidates.append((path, text))

        if not candidates:
            return ""

        # Attempt embedding search.
        query_vec = self._embed(query[:2000])
        if query_vec:
            # Backfill any entries missing from the DB (lazy migration).
            for path, text in candidates:
                self._embed_entry(path, text)

            # Load all vectors in one DB round-trip.
            db_vecs = dict(self._db_all_vecs())
            text_by_name = {p.name: t for p, t in candidates}

            scored_embed: list[tuple[float, float, Path, str]] = []
            for path, text in candidates:
                vec = db_vecs.get(path.name)
                if vec is None:
                    continue
                sim = self._cosine(query_vec, vec)
                scored_embed.append((sim, path.stat().st_mtime, path, text))

            MIN_SIM = 0.65
            scored_embed = [s for s in scored_embed if s[0] >= MIN_SIM]
            scored_embed.sort(key=lambda s: (s[0], s[1]), reverse=True)

            if scored_embed:
                return self._format_snippets(scored_embed[:limit], max_chars)

        # Fallback: keyword overlap.
        scored_kw: list[tuple[int, float, Path, str]] = []
        for path, text in candidates:
            haystack = f"{path.stem}\n{text}".lower()
            score = sum(haystack.count(term) for term in terms)
            if score:
                scored_kw.append((score, path.stat().st_mtime, path, text))
        scored_kw.sort(key=lambda s: (s[0], s[1]), reverse=True)
        return self._format_snippets(scored_kw[:limit], max_chars)

    def _format_snippets(
        self,
        scored: list[tuple],
        max_chars: int,
    ) -> str:
        """Render top-k scored entries as labelled markdown snippets."""
        snippets: list[str] = []
        now_ts = time.time()
        for row in scored:
            path, text = row[-2], row[-1]
            body = self._strip_frontmatter(text).strip()
            if len(body) > max_chars:
                body = body[:max_chars].rstrip() + "\n[...truncated]"
            mtime = path.stat().st_mtime
            age_days = max(0, int((now_ts - mtime) / 86400))
            age_tag = f"updated {age_days}d ago" if age_days > 0 else "updated today"
            stale = " · ⚠ stale" if age_days >= 30 else ""
            name_match = re.search(r"^name:\s*(.+)$", text, re.MULTILINE)
            label = name_match.group(1).strip() if name_match else path.stem
            snippets.append(f"## {label} ({age_tag}{stale})\n{body}")
        return "\n\n".join(snippets)

    @staticmethod
    def _query_terms(query: str) -> list[str]:
        words = re.findall(r"[a-z0-9_]{3,}", query.lower())
        stop = {
            "the", "and", "for", "that", "this", "you", "your", "are",
            "can", "what", "when", "where", "how", "why", "with",
            "from", "about", "remember", "know",
        }
        seen: set[str] = set()
        terms: list[str] = []
        for word in words:
            if word in stop or word in seen:
                continue
            seen.add(word)
            terms.append(word)
        return terms[:12]

    @staticmethod
    def _strip_frontmatter(text: str) -> str:
        if not text.startswith("---\n"):
            return text
        end = text.find("\n---\n", 4)
        if end == -1:
            return text
        return text[end + 5:]

    # ---- write side ----------------------------------------------------

    def remember(
        self,
        name: str,
        description: str,
        type: str,
        body: str,
        related: list[str] | None = None,
    ) -> str:
        """Save a memory entry and update the index. Returns a status string.

        If a memory file with the same generated filename already exists,
        we overwrite it — letting the agent "update" memories rather than
        accumulate duplicates.

        `related` is an optional list of memory slugs (filenames without
        .md extension) that this entry connects to. Stored in frontmatter
        AND rendered as a [[wikilink]] section at the bottom of the body
        so Obsidian's graph view picks up the relationships.
        """
        if type not in ALLOWED_TYPES:
            return f"ERROR: type must be one of {sorted(ALLOWED_TYPES)}, got '{type}'"

        # Reject failure/confusion states — these are task scratch-notes, not durable facts.
        # A real memory should state what IS true, not what the agent couldn't figure out.
        _FAILURE_PHRASES = (
            "unable to locate", "unable to find", "could not locate", "could not find",
            "cannot find", "need to find", "need to locate", "need to check",
            "please check for", "i need to", "we need to", "not sure where",
        )
        body_lower = body.lower()
        for phrase in _FAILURE_PHRASES:
            if phrase in body_lower:
                return (
                    f"ERROR: memory body looks like a failure state or an unresolved note "
                    f"(contains '{phrase}'). Resolve the problem first, then save the result. "
                    "Memories must record what IS true, not what you still need to do."
                )

        # Strip redundant type prefix from name if the LLM included it.
        # e.g. name="project_delivered_problems", type="project"
        # → slug="delivered_problems" → filename="project_delivered_problems.md"
        clean_name = name
        for t in ALLOWED_TYPES:
            while clean_name.lower().startswith(f"{t}_"):
                clean_name = clean_name[len(t) + 1:]
        slug = self._slugify(clean_name)
        filename = f"{type}_{slug}.md"
        path = self.root / filename

        # Normalize related: drop empty/None, strip .md suffix if the LLM
        # included it, drop any self-reference.
        rel_clean: list[str] = []
        for r in related or []:
            r = (r or "").strip()
            if r.endswith(".md"):
                r = r[:-3]
            if r and r != filename[:-3]:
                rel_clean.append(r)

        frontmatter_related = ""
        related_section = ""
        if rel_clean:
            frontmatter_related = "related: [" + ", ".join(rel_clean) + "]\n"
            wikilinks = "\n".join(f"- [[{r}]]" for r in rel_clean)
            related_section = f"\n\n## Related\n{wikilinks}"

        content = (
            f"---\nname: {name}\ndescription: {description}\ntype: {type}\n"
            f"{frontmatter_related}---\n\n{body.strip()}{related_section}\n"
        )

        # Read old body BEFORE overwriting so we can echo it back.
        # This lets the agent self-correct if it accidentally replaced a list
        # with a single entry — it can see immediately what it overwrote.
        old_body_preview: str | None = None
        if path.exists():
            try:
                old_text = path.read_text(encoding="utf-8")
                # Extract body (text after second "---\n")
                parts = old_text.split("---\n", 2)
                if len(parts) == 3:
                    old_body = parts[2].strip()
                    if old_body:
                        old_body_preview = old_body[:200] + ("…" if len(old_body) > 200 else "")
            except Exception:
                pass

        path.write_text(content, encoding="utf-8")
        # Re-embed: delete existing row so _embed_entry will recompute.
        self._db_delete_vec(path.name)
        self._embed_entry(path, content)
        # Dedup hint: if the new embedding is very similar to an existing entry,
        # append a note so the agent knows it may have created a near-duplicate.
        dedup_note = ""
        new_vec = self._db_load_vec(path.name)
        if new_vec:
            warn = self._dedup_check(path.name, new_vec)
            if warn:
                dedup_note = f"\n\n{warn}"
        self._upsert_index_entry(name, description, filename)

        action = "updated" if old_body_preview is not None else "created"
        if _events:
            _events.emit("memory_write", name=filename, memory_name=name,
                         description=description, memory_type=type, action=action)

        if old_body_preview is not None:
            return (
                f"Saved memory '{name}' to {filename} (overwrote previous content: {old_body_preview!r}). "
                f"If you replaced a list, verify the new body includes all prior entries.{dedup_note}"
            )
        return f"Saved memory '{name}' to {filename}{dedup_note}"

    def forget(self, identifier: str) -> str:
        """Delete a memory by name OR by filename. Returns a status string.

        Accepts either the human name ("User Role") or the filename
        ("user_user_role.md" / "user_user_role") — we resolve to a
        filename and remove BOTH the body file and the index line.

        Idempotent: removing an already-gone memory returns a benign
        status rather than raising. This way the agent can call forget()
        confidently from the reflection loop without exception handling.
        """
        candidates = self._resolve_filename(identifier)
        if not candidates:
            return f"Memory '{identifier}' not found (already gone?)"

        removed: list[str] = []
        for filename in candidates:
            path = self.root / filename
            if path.exists():
                path.unlink()
            self._db_delete_vec(filename)
            self._remove_index_entry(filename)
            removed.append(filename)
        if _events and removed:
            _events.emit("memory_forget", name=removed[0],
                         text=f"forgot: {', '.join(removed)}")
        return f"Forgot memory: {', '.join(removed)}"

    def _resolve_filename(self, identifier: str) -> list[str]:
        """Find memory filenames matching a name or filename hint.

        Resolution order:
          1. Exact filename match (e.g. "user_role.md")
          2. Filename without .md (e.g. "user_role")
          3. Slug match against any type prefix (e.g. "User Role" →
             check user_user_role.md, feedback_user_role.md, ...)

        Returns a list because in rare cases the same slug exists across
        multiple type prefixes — caller deletes all matches.
        """
        ident = identifier.strip()
        if ident.endswith(".md"):
            return [ident] if (self.root / ident).exists() else []
        # Try as bare filename
        candidate = ident + ".md"
        if (self.root / candidate).exists():
            return [candidate]
        # Try as a human name — slug it and check each type prefix
        slug = self._slugify(ident)
        matches: list[str] = []
        for type_prefix in ALLOWED_TYPES:
            fname = f"{type_prefix}_{slug}.md"
            if (self.root / fname).exists():
                matches.append(fname)
        return matches

    def _remove_index_entry(self, filename: str) -> None:
        """Drop the MEMORY.md line that links to this filename."""
        if not self.index_path.exists():
            return
        current = self.index_path.read_text(encoding="utf-8")
        kept = [
            line for line in current.splitlines()
            if f"({filename})" not in line and f"(./{filename})" not in line and f"(memory/{filename})" not in line
        ]
        # Preserve header (non-entry lines) + remaining entries.
        self.index_path.write_text("\n".join(kept) + "\n", encoding="utf-8")

    # ---- internals -----------------------------------------------------

    @staticmethod
    def _slugify(s: str) -> str:
        """Convert a name like 'My Favorite Food' to 'my_favorite_food'."""
        slug = re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")
        return slug[:50] or "untitled"

    @staticmethod
    def _humanize_age(mtime: float) -> str:
        """Turn a file mtime into a plain-language age string.

        LLMs are bad at date arithmetic. 'today' / '3 days ago' triggers
        staleness reasoning that '2026-04-22T14:33:01' doesn't.
        """
        days = int((time.time() - mtime) // 86400)
        if days <= 0:
            return "today"
        if days == 1:
            return "yesterday"
        if days < 14:
            return f"{days} days ago"
        if days < 60:
            return f"~{days // 7} weeks ago"
        if days < 365:
            return f"~{days // 30} months ago"
        years = days // 365
        return f"over {years} year{'s' if years > 1 else ''} ago"

    def _annotate_entry(self, line: str) -> str:
        """Add an age tag and staleness marker to a memory index line."""
        match = re.match(r"^- \[([^\]]+)\]\((?:\./|memory/)([^)]+)\) — (.+)$", line)
        if not match:
            return line  # not an entry line — pass through unchanged
        title, filename, desc = match.groups()
        path = self.root / filename
        if not path.exists():
            return line  # broken link — leave it alone for now
        mtime = path.stat().st_mtime
        age = self._humanize_age(mtime)
        stale = " ⚠ may be stale" if (time.time() - mtime) > 30 * 86400 else ""
        return f"- [{title}](memory/{filename}) *({age}{stale})* — {desc}"

    def _upsert_index_entry(self, name: str, description: str, filename: str) -> None:
        """Add or replace this entry's line in MEMORY.md.

        Each line carries BOTH a markdown link (for plain-text viewers and
        our own parsing) and an Obsidian [[wikilink]] (for Obsidian's
        backlink resolver / graph view). Two link styles, one line.
        """
        stem = filename[:-3] if filename.endswith(".md") else filename
        line = f"- [{name}](memory/{filename}) — {description} [[{stem}]]"
        current = self.index_path.read_text(encoding="utf-8")
        # Drop any existing line referencing this filename (for upserts).
        kept = [
            existing for existing in current.splitlines()
            if f"({filename})" not in existing
        ]
        # Reconstruct: keep header + non-blank entry lines + this new line.
        entries = [l for l in kept if l.startswith("- ")]
        entries.append(line)
        entries.sort()  # deterministic order
        self.index_path.write_text(
            _INDEX_HEADER + "\n".join(entries) + "\n",
            encoding="utf-8",
        )
