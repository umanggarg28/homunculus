"""
Persistent memory for Homunculus.

The memory layout:

    memory/
        MEMORY.md           # index — one line per entry, loaded into prompt
        user_<slug>.md      # facts about the user (role, preferences, etc.)
        feedback_<slug>.md  # collaboration rules from user corrections
        project_<slug>.md   # ongoing work context (decays over time)
        reference_<slug>.md # pointers to external resources

Each memory file has YAML-ish frontmatter:

    ---
    name: <title>
    description: <one-line summary>
    type: user|feedback|project|reference
    ---

    <body>

The split is deliberate: MEMORY.md is ALWAYS loaded into the system prompt
so the agent knows what it knows. Full bodies are read on demand (via the
existing read_file tool) when the agent decides a specific memory is
relevant. This keeps context usage tiny even as memory grows.
"""

import re
import time
from datetime import datetime
from pathlib import Path

ALLOWED_TYPES = {"user", "feedback", "project", "reference"}

_INDEX_HEADER = "# Memory\n\nThis index lists every durable fact I've remembered. Full bodies live in the linked files; use read_file to fetch one when relevant.\n\n"


class Memory:
    """Disk-backed memory store. One instance per agent."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = root / "MEMORY.md"
        if not self.index_path.exists():
            self.index_path.write_text(_INDEX_HEADER, encoding="utf-8")

    # ---- read side -----------------------------------------------------

    def load_index(self) -> str:
        """Return MEMORY.md content with age annotations on each entry.

        We don't store ages in the file (they'd be stale instantly). Instead
        we inject "(N days ago)" at read time based on the linked file's
        actual mtime. Old memories also get a 'stale' marker so the LLM
        knows to double-check them.
        """
        raw = self.index_path.read_text(encoding="utf-8")
        annotated_lines = [self._annotate_entry(line) for line in raw.splitlines()]
        return "\n".join(annotated_lines) + "\n"

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

    # ---- write side ----------------------------------------------------

    def remember(self, name: str, description: str, type: str, body: str) -> str:
        """Save a memory entry and update the index. Returns a status string.

        If a memory file with the same generated filename already exists,
        we overwrite it — letting the agent "update" memories rather than
        accumulate duplicates.
        """
        if type not in ALLOWED_TYPES:
            return f"ERROR: type must be one of {sorted(ALLOWED_TYPES)}, got '{type}'"

        slug = self._slugify(name)
        filename = f"{type}_{slug}.md"
        path = self.root / filename

        path.write_text(
            f"---\nname: {name}\ndescription: {description}\ntype: {type}\n---\n\n{body.strip()}\n",
            encoding="utf-8",
        )
        self._upsert_index_entry(name, description, filename)
        return f"Saved memory '{name}' to {filename}"

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
        match = re.match(r"^- \[([^\]]+)\]\(\./([^)]+)\) — (.+)$", line)
        if not match:
            return line  # not an entry line — pass through unchanged
        title, filename, desc = match.groups()
        path = self.root / filename
        if not path.exists():
            return line  # broken link — leave it alone for now
        mtime = path.stat().st_mtime
        age = self._humanize_age(mtime)
        stale = " ⚠ may be stale" if (time.time() - mtime) > 30 * 86400 else ""
        return f"- [{title}](./{filename}) *({age}{stale})* — {desc}"

    def _upsert_index_entry(self, name: str, description: str, filename: str) -> None:
        """Add or replace this entry's line in MEMORY.md."""
        line = f"- [{name}](./{filename}) — {description}"
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
