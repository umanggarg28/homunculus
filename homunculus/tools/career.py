"""Career context + job-posting tools (the apply-assist capability).

Two knowledge sources, one discipline each:

- ``career_context`` reads the career-ops repo, mounted read-only at
  ``HOMUNCULUS_CAREER_DIR``. The repo is the single source of truth
  (the "personal wiki written for the agent" pattern): the agent reads
  it live on every call and never copies it into memory, so an edit to
  CAREER-CONTEXT.md is visible on the next tool call with no sync step.

- ``job_posting`` resolves a pasted application link against the big
  ATS platforms' PUBLIC JSON APIs (Greenhouse, Lever) — structured
  title/location/questions, not scraped HTML — and falls back to a
  plain page fetch elsewhere. Posting content is third-party text and
  is treated as untrusted (see security._UNTRUSTED_CONTENT_TOOLS).

Both degrade to sentinels the playbooks know ("CAREER CONTEXT
UNAVAILABLE" / "POSTING UNAVAILABLE") rather than raising.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import httpx

log = logging.getLogger("homunculus")

CONTEXT_UNAVAILABLE = "CAREER CONTEXT UNAVAILABLE"
POSTING_UNAVAILABLE = "POSTING UNAVAILABLE"

#: Files read from the career repo, in order. CAREER-CONTEXT.md is the
#: wiki; cv.md is the factual record forms get filled from.
_CONTEXT_FILES = ("CAREER-CONTEXT.md", "cv.md")
_MAX_CHARS = 9000


def _career_dir() -> Path:
    return Path(os.environ.get("HOMUNCULUS_CAREER_DIR", "/app/career"))


def career_context(section: str = "") -> str:
    """The user's career wiki (context + CV), optionally one section.

    `section` filters to `## <heading>` blocks whose heading contains
    the given text, case-insensitively, across both files.
    """
    root = _career_dir()
    parts: list[str] = []
    for name in _CONTEXT_FILES:
        p = root / name
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if section:
            blocks = _matching_sections(text, section)
            if blocks:
                parts.append(f"[{name}]\n" + "\n\n".join(blocks))
        else:
            parts.append(f"[{name}]\n{text}")
    if not parts:
        if section:
            return (
                f"No section matching {section!r} in the career wiki. "
                "Call career_context() with no arguments to see everything."
            )
        return (
            f"{CONTEXT_UNAVAILABLE} — career repo not mounted or empty. "
            "Omit career details rather than inventing them."
        )
    out = "\n\n────\n\n".join(parts)
    if len(out) > _MAX_CHARS:
        out = out[:_MAX_CHARS] + "\n…[truncated — ask for a specific section]"
    return out


def _matching_sections(text: str, needle: str) -> list[str]:
    """`## `-delimited blocks whose heading contains `needle` (ci)."""
    blocks: list[str] = []
    current_heading: str | None = None
    current: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if current_heading is not None:
                blocks.append("\n".join(current).strip())
            current_heading = line[3:].strip()
            current = [line]
        elif current_heading is not None:
            current.append(line)
    if current_heading is not None:
        blocks.append("\n".join(current).strip())
    n = needle.lower()
    # Match heading OR body: the fact a caller wants ("visa") usually
    # lives inside a section named something else ("The Situation") —
    # heading-only matching sent the live query home empty-handed.
    return [b for b in blocks if n in b.lower()]


# ── job postings ─────────────────────────────────────────────────────

_GREENHOUSE_RE = re.compile(
    r"(?:job-boards|boards)\.greenhouse\.io/(?:embed/job_app\?[^ ]*for=)?"
    r"(?P<org>[A-Za-z0-9_-]+)/jobs/(?P<id>\d+)"
)
_LEVER_RE = re.compile(
    r"jobs\.(?:eu\.)?lever\.co/(?P<org>[A-Za-z0-9_-]+)/(?P<id>[0-9a-f-]{36})"
)


def job_posting(url: str) -> str:
    """Digest a job posting URL: title, location, description, and (on
    Greenhouse) the application form's actual question list."""
    url = (url or "").strip()
    if not url:
        return f"{POSTING_UNAVAILABLE} — empty URL."

    gh = _GREENHOUSE_RE.search(url)
    if gh:
        return _greenhouse_posting(gh.group("org"), gh.group("id"))
    lv = _LEVER_RE.search(url)
    if lv:
        return _lever_posting(lv.group("org"), lv.group("id"))

    # Unknown platform — plain fetch so the agent still gets the JD.
    from homunculus.tools import web

    text = web.web_fetch(url)
    return f"[platform: unknown — description only, no form schema]\n{text}"


def _get_json(url: str, params: dict | None = None) -> dict | None:
    try:
        resp = httpx.get(url, params=params or {}, timeout=20)
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, ValueError) as e:
        log.warning(f"[career] GET {url} failed: {e}")
        return None


def _strip_html(html: str, cap: int = 4000) -> str:
    from bs4 import BeautifulSoup

    text = BeautifulSoup(html, "lxml").get_text("\n")
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:cap]


def _greenhouse_posting(org: str, job_id: str) -> str:
    data = _get_json(
        f"https://boards-api.greenhouse.io/v1/boards/{org}/jobs/{job_id}",
        {"questions": "true"},
    )
    if not data:
        return f"{POSTING_UNAVAILABLE} — Greenhouse API returned nothing for {org}/{job_id}."
    lines = [
        "[platform: greenhouse]",
        f"org: {org} · job_id: {job_id}",
        f"title: {data.get('title', '?')}",
        f"location: {(data.get('location') or {}).get('name', '?')}",
    ]
    questions = data.get("questions") or []
    if questions:
        lines.append("\nform questions (the ACTUAL application form):")
        for q in questions:
            req = "required" if q.get("required") else "optional"
            fields = q.get("fields") or []
            ftype = fields[0].get("type", "?") if fields else "?"
            lines.append(f"  - {q.get('label', '?')} [{ftype} · {req}]")
    import html as _html

    lines.append("\ndescription:\n" + _strip_html(_html.unescape(data.get("content") or "")))
    return "\n".join(lines)


def _lever_posting(org: str, posting_id: str) -> str:
    data = _get_json(f"https://api.lever.co/v0/postings/{org}/{posting_id}")
    if not data:
        return f"{POSTING_UNAVAILABLE} — Lever API returned nothing for {org}/{posting_id}."
    cats = data.get("categories") or {}
    lines = [
        "[platform: lever]",
        f"org: {org} · posting_id: {posting_id}",
        f"title: {data.get('text', '?')}",
        f"location: {cats.get('location', '?')} · team: {cats.get('team', '?')} · commitment: {cats.get('commitment', '?')}",
        f"apply_url: {data.get('applyUrl', '')}",
        "\ndescription:\n" + _strip_html(data.get("description") or ""),
    ]
    for l in data.get("lists") or []:
        lines.append(f"\n{l.get('text', '')}:\n" + _strip_html(l.get("content") or "", cap=1200))
    return "\n".join(lines)[: _MAX_CHARS]
