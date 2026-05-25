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

    # ---- pending notifications queue -----------------------------------
    # When `notify()` fires (typically from the heartbeat daemon), it sends
    # a Telegram message but doesn't touch the Telegram bot's in-memory
    # `_agent.history`. Without bridging, a follow-up like "explain it"
    # arrives with zero context and the agent confabulates an unrelated
    # answer. We append every notification to this jsonl queue; the
    # Telegram bot drains entries newer than `_notifications_consumed_ts`
    # into its history before processing the next user message.

    @property
    def notifications_path(self) -> Path:
        return self.root / "_notifications.jsonl"

    @property
    def _notifications_pointer_path(self) -> Path:
        return self.root / "_notifications_consumed_ts.txt"

    @property
    def _notifications_lock_path(self) -> Path:
        return self.root / "_notifications.lock"

    def queue_notification(self, text: str) -> None:
        """Append a notification to the persistent queue.

        Safe to call from any process (heartbeat, telegram bot, web).
        The Telegram bot (and web API) drain via
        `drain_pending_notifications` before processing each user message.

        POSIX O_APPEND on a single line is atomic up to PIPE_BUF (4096
        bytes), so concurrent appenders won't interleave for normal-
        sized messages. Locking is only needed for the drain pointer.
        """
        self.notifications_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"ts": time.time(), "text": text}
        with self.notifications_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def drain_pending_notifications(self) -> list[dict]:
        """Return notifications sent since the last drain.

        Reads the consumption pointer (a unix-timestamp string), returns
        entries newer than that pointer, then advances the pointer to the
        timestamp of the newest returned entry.

        Atomically protected by a lock so two drainers (Telegram bot and
        Web API both running) don't both see the same entries and double-
        inject them.

        Each returned dict has keys `ts` (float) and `text` (str).
        Returns [] if the queue is empty or all entries are already
        consumed.
        """
        if not self.notifications_path.exists():
            return []
        with self._file_lock(self._notifications_lock_path):
            last_ts = 0.0
            if self._notifications_pointer_path.exists():
                try:
                    last_ts = float(self._notifications_pointer_path.read_text(encoding="utf-8").strip())
                except (ValueError, OSError):
                    last_ts = 0.0
            fresh: list[dict] = []
            try:
                with self.notifications_path.open("r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(entry, dict):
                            continue
                        try:
                            ts = float(entry.get("ts", 0))
                        except (TypeError, ValueError):
                            continue
                        if ts > last_ts:
                            fresh.append(entry)
            except OSError:
                return []
            if not fresh:
                return []
            new_ts = max(float(e["ts"]) for e in fresh)
            tmp = self._notifications_pointer_path.with_suffix(".tmp")
            try:
                tmp.write_text(f"{new_ts}\n", encoding="utf-8")
                tmp.replace(self._notifications_pointer_path)
            except OSError:
                pass
            return fresh

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

    def peek_next_tick(self) -> str | None:
        """Read the target wake time without consuming it (UI display)."""
        if not self.next_tick_path.exists():
            return None
        try:
            return self.next_tick_path.read_text(encoding="utf-8").strip() or None
        except Exception:
            return None

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

    # ---- embedding-based search ----------------------------------------
    # Uses Gemini text-embedding-004 (free tier: 1500 RPD, 768-dim vectors).
    # Vectors are cached as .vec sidecar files next to each .md entry so
    # search only costs ONE API call (the query) per invocation.
    # Falls back to keyword search if the Gemini key is missing or the
    # API is unavailable.

    _EMBED_MODEL = "models/gemini-embedding-2"
    _EMBED_URL = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-embedding-2:embedContent?key={key}"
    )
    _EMBED_DIM = 3072

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

    def _vec_path(self, md_path: Path) -> Path:
        return md_path.with_suffix(".vec")

    def _load_vec(self, md_path: Path) -> list[float] | None:
        vp = self._vec_path(md_path)
        if not vp.exists():
            return None
        try:
            return json.loads(vp.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _save_vec(self, md_path: Path, vec: list[float]) -> None:
        try:
            self._vec_path(md_path).write_text(json.dumps(vec), encoding="utf-8")
        except Exception:
            pass

    def _embed_entry(self, path: Path, text: str) -> None:
        """Compute and cache the embedding for a memory file if not already cached."""
        if self._vec_path(path).exists():
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
            self._save_vec(path, vec)

    def search(self, query: str, limit: int = 3, max_chars: int = 900) -> str:
        """Return the most relevant memory snippets for a query.

        Uses semantic embedding similarity (Gemini text-embedding-004) when
        available. Falls back to keyword overlap when the API is unreachable.
        Vectors are pre-computed on write and cached as .vec sidecars, so
        this method only costs one embedding API call (the query).
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
            # Backfill any entries that don't have vectors yet (lazy migration).
            for path, text in candidates:
                self._embed_entry(path, text)

            scored_embed: list[tuple[float, float, Path, str]] = []
            for path, text in candidates:
                vec = self._load_vec(path)
                if vec is None:
                    continue
                sim = self._cosine(query_vec, vec)
                scored_embed.append((sim, path.stat().st_mtime, path, text))

            # Only keep results above a minimum similarity threshold.
            # Below 0.65 the match is likely irrelevant noise.
            MIN_SIM = 0.65
            scored_embed = [s for s in scored_embed if s[0] >= MIN_SIM]
            scored_embed.sort(key=lambda s: (s[0], s[1]), reverse=True)

            if scored_embed:
                return self._format_snippets(scored_embed[:limit], max_chars)

        # Fallback: keyword overlap (original approach).
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
        path.write_text(content, encoding="utf-8")
        # Pre-compute and cache the embedding so the first search is instant.
        # We invalidate any stale .vec file first (content changed on upsert).
        vec_p = self._vec_path(path)
        if vec_p.exists():
            vec_p.unlink()
        self._embed_entry(path, content)
        self._upsert_index_entry(name, description, filename)
        return f"Saved memory '{name}' to {filename}"

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
            # Clean up the embedding sidecar too.
            vec_p = self._vec_path(path)
            if vec_p.exists():
                vec_p.unlink()
            self._remove_index_entry(filename)
            removed.append(filename)
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
            if f"({filename})" not in line and f"(./{filename})" not in line
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
