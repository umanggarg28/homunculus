"""Skill auto-refinement (T1.2 of capability roadmap).

When a task fails, find its associated skill memory (heuristic name-overlap
match) and append a "Watch out:" line citing the failure reason. Pure
filesystem; no imports of memory.py / agent / LLM — safe to call from any
post-failure code path (heartbeat post-tick check, the record_failure
tool wrapper, future explicit calls).

Idempotent: the same reason will not be appended twice.

Pattern is from Hermes Agent's evaluate→refine→retrieve loop. Mirroring
their shape exactly so the codebase stays familiar to anyone reading
both projects.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

# Maximum number of "Watch out:" notes to keep per skill. Older ones are
# pruned when the cap is reached — a skill with 20 failure notes is no
# longer useful learning, it's just noise.
MAX_WATCH_OUTS_PER_SKILL = 8

# Cap on a single watch-out reason — keep notes scannable.
MAX_REASON_CHARS = 240


_SLUG_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(name: str) -> set[str]:
    """Tokenize a slug like 'daily-leetcode-150-problem-at-9-am-ist' into
    its lowercase alphanumeric pieces.

    Filters short fillers (≤ 2 chars) and pure-digit tokens — they're
    noise in the overlap score, not signal."""
    raw = _SLUG_TOKEN.findall(name.lower())
    return {
        t for t in raw
        if len(t) > 2 and not t.isdigit() and t not in _STOP_TOKENS
    }


_STOP_TOKENS = {
    "the", "and", "for", "with", "task", "skill", "deliver", "from", "into",
    "this", "that", "every", "daily", "weekly", "morning",
}


def _find_matching_skill(task_id: str, memory_dir: Path) -> Path | None:
    """Return the skill_*.md file most likely to match `task_id`, or None.

    Heuristic: tokenize both names, pick the skill with the largest token
    overlap. Ties broken by recency (most-recently-modified wins). Returns
    None if no skill has at least 1 overlapping token.
    """
    task_tokens = _tokens(task_id)
    if not task_tokens:
        return None
    best: tuple[int, float, Path] | None = None
    for skill_path in memory_dir.glob("skill_*.md"):
        skill_tokens = _tokens(skill_path.stem)
        overlap = len(task_tokens & skill_tokens)
        if overlap == 0:
            continue
        mtime = skill_path.stat().st_mtime
        candidate = (overlap, mtime, skill_path)
        if best is None or candidate > best:
            best = candidate
    return best[2] if best else None


_WATCH_OUT_PATTERN = re.compile(
    r"^\* watch out\s*\((?P<date>\d{4}-\d{2}-\d{2})\)\s*:\s*(?P<reason>.+)$",
    re.IGNORECASE,
)


def _existing_reasons(body: str) -> list[str]:
    """Extract the reasons from current 'Watch out (date): reason' lines."""
    out = []
    for line in body.splitlines():
        m = _WATCH_OUT_PATTERN.match(line.strip())
        if m:
            out.append(m.group("reason").strip())
    return out


def _trim_reason(reason: str) -> str:
    s = " ".join(reason.split())  # collapse whitespace
    if len(s) > MAX_REASON_CHARS:
        s = s[: MAX_REASON_CHARS - 1] + "…"
    return s


def update_skill_on_failure(
    task_id: str,
    reason: str,
    memory_dir: str | os.PathLike[str] | None = None,
) -> str | None:
    """Append a Watch-out note to the skill associated with `task_id`.

    Returns the skill file path that was updated, or None if no matching
    skill was found / the reason was a duplicate / write failed.

    No-op when:
      - reason is empty
      - no skill_*.md has any token overlap with task_id
      - the same reason is already present (idempotent)
    """
    reason_clean = _trim_reason(reason or "")
    if not reason_clean:
        return None
    base = Path(memory_dir) if memory_dir else Path(
        os.environ.get("HOMUNCULUS_MEMORY_DIR", "./memory")
    )
    if not base.exists():
        return None
    skill_path = _find_matching_skill(task_id, base)
    if skill_path is None:
        return None
    try:
        body = skill_path.read_text(encoding="utf-8")
    except OSError:
        return None
    existing = _existing_reasons(body)
    # Dedup by exact match — if the reason is already there, do nothing.
    if any(reason_clean == r for r in existing):
        return None
    today = datetime.now().strftime("%Y-%m-%d")
    new_line = f"* Watch out ({today}): {reason_clean}"
    # If a "## Watch outs" section exists, append into it (just before
    # the next ## header or EOF). Otherwise append a new section.
    sections = re.split(r"(?m)^## ", body)
    # sections[0] is preamble; the rest each start with the heading text.
    watch_idx = None
    for i, sec in enumerate(sections):
        if i == 0:
            continue
        if sec.lower().startswith("watch outs"):
            watch_idx = i
            break
    if watch_idx is not None:
        # Append the new line to the end of this section.
        sec_body = sections[watch_idx].rstrip()
        # Prune old watch-outs if we're at the cap.
        lines = sec_body.splitlines()
        watch_lines = [l for l in lines if l.lstrip().lower().startswith("* watch out")]
        if len(watch_lines) >= MAX_WATCH_OUTS_PER_SKILL:
            # Drop the oldest (first) watch-out line.
            for j, l in enumerate(lines):
                if l.lstrip().lower().startswith("* watch out"):
                    del lines[j]
                    break
            sec_body = "\n".join(lines)
        sections[watch_idx] = sec_body + "\n" + new_line + "\n"
        new_body = sections[0] + "## " + "## ".join(sections[1:])
    else:
        # No existing watch-outs section — add one at the end.
        new_body = body.rstrip() + (
            "\n\n## Watch outs\n\n"
            "(auto-appended by the skill refiner; T1.2)\n\n"
            + new_line + "\n"
        )
    try:
        skill_path.write_text(new_body, encoding="utf-8")
    except OSError:
        return None
    return str(skill_path)
