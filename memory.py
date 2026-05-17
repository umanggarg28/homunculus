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

import json
import re
import time
from datetime import datetime
from pathlib import Path

ALLOWED_TYPES = {"user", "feedback", "project", "reference"}

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

    # ---- read side -----------------------------------------------------

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
        match = re.match(r"^- \[[^\]]+\]\(\./([^)]+)\)", line)
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

    def save_session(self, history: list[dict]) -> None:
        """Persist the conversation history (excluding the system prompt).

        The system prompt is regenerated fresh each time an agent starts
        (with current memory index, etc.), so we don't save it.
        """
        body = [msg for msg in history if msg.get("role") != "system"]
        self.session_path.write_text(json.dumps(body), encoding="utf-8")

    def load_session(self) -> list[dict]:
        """Return the previously-saved messages, or [] if no session file."""
        if not self.session_path.exists():
            return []
        try:
            return json.loads(self.session_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            # File got corrupted somehow — pretend it doesn't exist
            return []

    def clear_session(self) -> None:
        """Delete the saved session. Called when the user types reset."""
        if self.session_path.exists():
            self.session_path.unlink()

    # ---- self-scheduled heartbeat --------------------------------------
    # The heartbeat daemon can be told (by the agent itself, via the
    # schedule_next_tick tool) when to wake up next. We store the target
    # ISO datetime in a single-line file at memory/_next_tick.txt.

    @property
    def next_tick_path(self) -> Path:
        return self.root / "_next_tick.txt"

    def set_next_tick(self, iso_datetime: str) -> None:
        """Persist the target wake time."""
        self.next_tick_path.write_text(iso_datetime.strip(), encoding="utf-8")

    def pop_next_tick(self) -> str | None:
        """Return the target wake time and delete the file.

        We pop (not just read) so each tick starts fresh — if the agent
        forgets to schedule itself next time, we fall back to the default
        interval instead of using a stale schedule.
        """
        if not self.next_tick_path.exists():
            return None
        try:
            value = self.next_tick_path.read_text(encoding="utf-8").strip()
        finally:
            self.next_tick_path.unlink()
        return value or None

    # ---- self-improvement / daily reflection ---------------------------
    # The heartbeat runs at most one reflection per calendar day. We
    # remember the last reflection date as a plain YYYY-MM-DD string in
    # memory/_last_reflection.txt so the daemon can decide whether the
    # next tick should reflect (review yesterday's logs and save feedback
    # memories) or run a normal proactive tick.

    @property
    def last_reflection_path(self) -> Path:
        return self.root / "_last_reflection.txt"

    def get_last_reflection_date(self) -> str | None:
        """Return the YYYY-MM-DD string of the last reflection, or None."""
        if not self.last_reflection_path.exists():
            return None
        value = self.last_reflection_path.read_text(encoding="utf-8").strip()
        return value or None

    def set_last_reflection_date(self, date_str: str) -> None:
        """Mark a YYYY-MM-DD as having had its reflection done."""
        self.last_reflection_path.write_text(date_str.strip(), encoding="utf-8")

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

        slug = self._slugify(name)
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

        path.write_text(
            f"---\nname: {name}\ndescription: {description}\ntype: {type}\n"
            f"{frontmatter_related}---\n\n{body.strip()}{related_section}\n",
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
        """Add or replace this entry's line in MEMORY.md.

        Each line carries BOTH a markdown link (for plain-text viewers and
        our own parsing) and an Obsidian [[wikilink]] (for Obsidian's
        backlink resolver / graph view). Two link styles, one line.
        """
        stem = filename[:-3] if filename.endswith(".md") else filename
        line = f"- [{name}](./{filename}) — {description} [[{stem}]]"
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
