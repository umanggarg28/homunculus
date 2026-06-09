"""Skill registry — versioned procedural memory.

Background
----------
The agent stores procedural knowledge as `skill_<slug>.md` files inside
the memory dir. These get re-read every relevant tick — they're the
"how to do task X" reference. Today they're plain markdown that anyone
(user, agent, future refinement loop) edits in place. Edits are
destructive: there's no way to see "what did the skill say a week ago"
or to roll back a bad change.

What this module adds
---------------------
Every save_skill() call:
  1. Archives the current file body to a versioned snapshot under
     `.skill_history/<name>/v<n>_<timestamp>.md`.
  2. Updates `.skill_history/<name>/_manifest.json` with version metadata
     (timestamp, rationale, source — manual / refinement-tick / user-edit /
     bootstrap, prior_version pointer).
  3. Atomically writes the new body to the canonical path.

That gives us:
  - Diffable history per skill.
  - Rollback to any prior version via revert_to().
  - Provenance tracking (which save was an autonomous refinement vs a
    user edit) so future automation can avoid refining a skill that the
    user just hand-tuned.

This is the storage layer only. PR 2 wires the refinement-mode agent
to call save_skill() with source="refinement-tick".

OSS lineage
-----------
OpenClaw's skill registry (each skill carries a `last_known_good_at`
timestamp); Hermes's "refine skill" stage in the 5-stage loop; Letta's
sleep-time agent pattern (a separate process versions and updates
memory based on observed outcomes).
"""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Literal

# Sources of a skill save. Tracked in the manifest so a future
# refinement run can avoid clobbering a skill the user just hand-edited.
SaveSource = Literal["manual", "refinement-tick", "user-edit", "bootstrap"]


class Skills:
    """Per-memory-dir registry. One instance per agent."""

    def __init__(self, memory_root: Path) -> None:
        self.root = Path(memory_root)
        self._history_root = self.root / ".skill_history"

    # ---- naming / paths -------------------------------------------------

    @staticmethod
    def _is_valid_name(name: str) -> bool:
        # Conservative: lowercase alnum + underscore + hyphen, must start
        # with `skill_`. Mirrors how existing skills are named on disk.
        return bool(re.fullmatch(r"skill_[a-z0-9_\-]+", name))

    def _skill_path(self, name: str) -> Path:
        if not self._is_valid_name(name):
            raise ValueError(
                f"invalid skill name {name!r} — must match `skill_<slug>` "
                f"with [a-z0-9_-]"
            )
        return self.root / f"{name}.md"

    def _history_dir(self, name: str) -> Path:
        return self._history_root / name

    def _manifest_path(self, name: str) -> Path:
        return self._history_dir(name) / "_manifest.json"

    # ---- read side ------------------------------------------------------

    def load(self, name: str) -> str | None:
        """Return current body, or None if no such skill exists."""
        p = self._skill_path(name)
        if not p.exists():
            return None
        return p.read_text(encoding="utf-8")

    def exists(self, name: str) -> bool:
        return self._skill_path(name).exists()

    def list_all(self) -> list[str]:
        """Every skill currently registered (current versions only)."""
        if not self.root.exists():
            return []
        return sorted(
            p.stem for p in self.root.glob("skill_*.md")
            if self._is_valid_name(p.stem)
        )

    def manifest(self, name: str) -> dict:
        """Full version manifest for one skill, or {} if no history yet."""
        p = self._manifest_path(name)
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def versions(self, name: str) -> list[dict]:
        """Ordered list of version records (oldest first). Each:
        {version, timestamp, rationale, source, body_path}"""
        m = self.manifest(name)
        versions = m.get("versions", [])
        return sorted(versions, key=lambda v: v.get("version", 0))

    def current_version(self, name: str) -> int:
        """Highest version number recorded for this skill (0 if no history)."""
        vs = self.versions(name)
        return max((v.get("version", 0) for v in vs), default=0)

    def get_version_body(self, name: str, version: int) -> str | None:
        """Return the body of a specific archived version, or None."""
        for v in self.versions(name):
            if v.get("version") == version:
                p = self.root / v["body_path"]
                if p.exists():
                    return p.read_text(encoding="utf-8")
        return None

    # ---- write side -----------------------------------------------------

    def save(
        self,
        name: str,
        body: str,
        *,
        source: SaveSource,
        rationale: str = "",
    ) -> int:
        """Write `body` as the new canonical skill. Archives the prior
        version (if any) under .skill_history/. Returns the new version
        number.

        Atomic: archive + manifest update + canonical rewrite all happen
        inside the manifest's lock so a concurrent reader never sees a
        half-applied save.
        """
        p = self._skill_path(name)
        history_dir = self._history_dir(name)
        history_dir.mkdir(parents=True, exist_ok=True)

        with self._manifest_lock(name):
            manifest = self.manifest(name)
            versions: list[dict] = list(manifest.get("versions", []))

            # Archive current body if there is one. The first save() for
            # a brand-new skill has no prior body to archive.
            now = datetime.now().isoformat(timespec="seconds")
            new_version = max((v.get("version", 0) for v in versions), default=0) + 1

            if p.exists():
                prior_body = p.read_text(encoding="utf-8")
                prior_version = new_version - 1
                # Suffix collisions across rapid saves (same second) are
                # extremely rare; the manifest version number is the
                # authoritative ID, the timestamp is for readability.
                prior_filename = (
                    f"v{prior_version:03d}_{now.replace(':', '-')}.md"
                )
                (history_dir / prior_filename).write_text(prior_body, encoding="utf-8")
            else:
                prior_version = None

            versions.append({
                "version": new_version,
                "timestamp": now,
                "source": source,
                "rationale": rationale,
                "prior_version": prior_version,
                # body_path is RELATIVE TO memory root so a moved memory
                # dir (e.g. test → prod migration) doesn't break the link.
                "body_path": str(
                    Path(".skill_history") / name / f"v{new_version:03d}_{now.replace(':', '-')}.md"
                ),
            })

            # Save the NEW body's own archived copy too, so the manifest's
            # body_path is meaningful from day 1 — not just "the prior
            # version's archive." Future revert_to(N) reads body_path for N.
            new_archive_name = f"v{new_version:03d}_{now.replace(':', '-')}.md"
            (history_dir / new_archive_name).write_text(body, encoding="utf-8")

            manifest["versions"] = versions
            manifest["latest_version"] = new_version
            tmp = self._manifest_path(name).with_suffix(".json.tmp")
            tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._manifest_path(name))

            # Atomic rewrite of the canonical file.
            tmp_skill = p.with_suffix(".md.tmp")
            tmp_skill.write_text(body, encoding="utf-8")
            tmp_skill.replace(p)

            return new_version

    def revert_to(self, name: str, version: int, *, rationale: str = "") -> int:
        """Roll back to a previous version's body. Recorded as a new
        save (source="user-edit" by convention so it's distinguishable
        from a forward-edit) so the revert itself is in the history."""
        body = self.get_version_body(name, version)
        if body is None:
            raise ValueError(f"no archived body for {name} v{version}")
        return self.save(
            name,
            body,
            source="user-edit",
            rationale=rationale or f"revert to v{version}",
        )

    # ---- locking --------------------------------------------------------

    @contextmanager
    def _manifest_lock(self, name: str) -> Iterator[None]:
        """Cross-process lock keyed on the skill's history dir.

        Single-writer cases (the typical refinement tick) don't need it
        functionally; we hold the lock around manifest read-modify-write
        so two refinement runs on the same skill can't collide.
        """
        # Reuse memory.py's _file_lock primitive via a fresh tiny impl —
        # we don't want skills.py importing memory.py and pulling in the
        # markdown-search subsystem.
        import errno
        import fcntl
        import time
        lock_path = self._history_dir(name) / ".lock"
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
                raise RuntimeError(
                    f"could not acquire skill manifest lock for {name} after 5s"
                )
            try:
                yield
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)


# ---- skill loader for the heartbeat state-machine path --------------
#
# Parses the YAML frontmatter of a `skill_*.md` file and returns the
# `states:` declaration (if any) plus the markdown body. The heartbeat
# tick uses this to drive the agent through a fixed tool sequence
# instead of relying on the model to recall its own playbook.
#
# Why a dedicated helper here rather than re-using memory.py's
# `_strip_frontmatter`: memory.py only strips and regex-extracts a
# couple of scalar fields. `states:` is a list-of-dicts that needs a
# proper YAML parse — string-matching would break on the first nested
# key.
#
# PyYAML 6.0.3 is already a transitive dep of the project; importing
# yaml directly keeps the loader self-contained.


def load_skill_playbook(
    memory_root: Path,
    skill_name: str,
) -> tuple[list[dict] | None, str]:
    """Return (states, body) for a skill file.

    - states: list of state dicts from the frontmatter's `states:` key,
      or None if the file has no `states:` declaration. Legacy skills
      without states continue to work via the free-form agent loop.
    - body: the markdown after the frontmatter (or the whole file if
      there is no frontmatter). This is what the heartbeat injects into
      the per-task prompt so the model has the playbook in context
      without needing to call recall() first.

    Raises FileNotFoundError if the skill file doesn't exist.
    """
    skill_path = memory_root / f"{skill_name}.md"
    text = skill_path.read_text(encoding="utf-8")

    # No frontmatter: whole file is the body, no states.
    if not text.startswith("---\n"):
        return None, text

    end = text.find("\n---\n", 4)
    if end == -1:
        # Malformed frontmatter (opening fence with no closing): treat
        # the whole file as body and surface no states. Caller will
        # fall back to free-form.
        return None, text

    frontmatter_text = text[4:end]
    body = text[end + 5:]

    import yaml
    try:
        parsed = yaml.safe_load(frontmatter_text) or {}
    except yaml.YAMLError:
        # Malformed YAML — degrade to legacy (no states).
        return None, body

    if not isinstance(parsed, dict):
        return None, body

    states = parsed.get("states")
    if states is None:
        return None, body
    if not isinstance(states, list):
        # Author error: `states:` must be a list. Fall back rather than
        # crash so a broken skill file doesn't take down the heartbeat.
        return None, body

    # Validate state shape minimally. Each entry must be a dict with
    # at least a `tool` key. Invalid entries → degrade to legacy.
    for s in states:
        if not isinstance(s, dict) or "tool" not in s:
            return None, body

    return states, body
