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

import json as _json
import logging
import os
import re
from datetime import datetime as _dt
from pathlib import Path

import httpx
from homunculus.sentinels import (
    CAREER_CONTEXT_UNAVAILABLE,
    POSTING_UNAVAILABLE as _POSTING_UNAVAILABLE,
)

log = logging.getLogger("homunculus")

# Re-exported under the names this module has always used; the tokens
# themselves live in the shared registry.
CONTEXT_UNAVAILABLE = CAREER_CONTEXT_UNAVAILABLE
POSTING_UNAVAILABLE = _POSTING_UNAVAILABLE

#: Files read from the career repo, in order. CAREER-CONTEXT.md is the
#: wiki; cv.md is the factual record forms get filled from.
_CONTEXT_FILES = (
    "CAREER-CONTEXT.md",
    "cv.md",
    # The user's rehearsed interview voice — better raw material for
    # "why us?" drafts than the CV's bullet prose.
    "interview-prep/narrative-playbook.md",
    "interview-prep/story-bank.md",
)
_MAX_CHARS = 14000


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


# ── application plans (apply-assist CP2) ─────────────────────────────
# prepare_application() builds a structured field→value plan:
# deterministic facts (name, email, links) are parsed straight out of
# the wiki's Personal table — never model-supplied — and the free-text
# questions are listed for the model to draft via draft_answer(). The
# host-side scripts/apply_fill.py reads the finished plan and fills the
# real form in a visible browser; submission stays with the human.

def _applications_dir() -> Path:
    from homunculus.tools._helpers import WORKSPACE_ROOT

    d = WORKSPACE_ROOT / "applications"
    d.mkdir(parents=True, exist_ok=True)
    return d


_PERSONAL_ROW_RE = re.compile(r"\|\s*\*\*(?P<key>[^*|]+)\*\*\s*\|\s*(?P<val>[^|]+)\|")

#: Questions the model must NEVER answer, even with options in hand.
#: Two categories: (1) visa/work-authorization and EEO/demographic
#: self-identification — a wrong answer is a legal misrepresentation
#: (the observed failure: the wiki says "H1-B transfer, no lottery"
#: and the model rounded that to "No sponsorship required", which is
#: false); (2) the applicant's history with this specific company
#: ("interviewed here before?", "referred by anyone?", "how did you
#: hear about this role?") — no document can contain these facts, and
#: the UNKNOWN instruction demonstrably fails to hold: models answer
#: a confident "No" instead. Both stay empty in the plan and are
#: decided by the human in the open browser.
_HUMAN_ONLY_RE = re.compile(
    r"sponsor|visa|work authoriz|legally authorized|citizen|immigration"
    r"|gender|race|ethnicit|veteran|disabilit|self.?identif"
    r"|interviewed (at|with|here|before)|previously (applied|worked|interviewed)"
    r"|applied (to|with|at|here|before)|referr|hear about",
    re.IGNORECASE,
)

#: wiki Personal-table keys → canonical form-field names.
_FIELD_ALIASES = {
    "name": "full_name",
    "email": "email",
    "phone": "phone",
    "linkedin": "linkedin",
    "github": "github",
    "location": "location",
}


def _personal_fields() -> dict[str, str]:
    """Contact facts parsed from the wiki's Personal table. Deterministic
    lookup — a form must never contain a model-guessed phone number."""
    p = _career_dir() / "CAREER-CONTEXT.md"
    if not p.is_file():
        return {}
    out: dict[str, str] = {}
    for m in _PERSONAL_ROW_RE.finditer(p.read_text(encoding="utf-8", errors="replace")):
        key = m.group("key").strip().lower()
        if key in _FIELD_ALIASES:
            out[_FIELD_ALIASES[key]] = m.group("val").strip()
    full = out.get("full_name", "")
    if full and " " in full:
        out.setdefault("first_name", full.split()[0])
        out.setdefault("last_name", full.split()[-1])
    return out


def _resume_path() -> str | None:
    """Newest PDF in the wiki's resume/ dir (container path)."""
    d = _career_dir() / "resume"
    if not d.is_dir():
        return None
    pdfs = sorted(d.glob("*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)
    return str(pdfs[0]) if pdfs else None


# Question labels the personal table can answer directly, by substring.
_LABEL_TO_FACT = [
    ("first name", "first_name"),
    ("last name", "last_name"),
    ("full name", "full_name"),
    ("email", "email"),
    ("phone", "phone"),
    ("linkedin", "linkedin"),
    ("github", "github"),
]


def prepare_application(url: str) -> str:
    """Create an application plan for a Greenhouse posting URL.

    Contact fields fill deterministically from the career wiki; the
    resume attaches from the wiki's resume/ dir; free-text questions
    are listed for draft_answer(). Returns the plan summary."""
    gh = _GREENHOUSE_RE.search((url or "").strip())
    if not gh:
        return (
            f"{POSTING_UNAVAILABLE} — prepare_application currently supports "
            "Greenhouse URLs only (their API exposes the form schema). For "
            "other platforms use job_posting(url) and draft answers in chat."
        )
    org, job_id = gh.group("org"), gh.group("id")
    data = _get_json(
        f"https://boards-api.greenhouse.io/v1/boards/{org}/jobs/{job_id}",
        {"questions": "true"},
    )
    if not data:
        return f"{POSTING_UNAVAILABLE} — Greenhouse API returned nothing for {org}/{job_id}."

    facts = _personal_fields()
    resume = _resume_path()
    fields: list[dict] = []
    needs_draft: list[str] = []
    for q in data.get("questions") or []:
        label = str(q.get("label") or "").strip()
        fl = label.lower()
        ftype = (q.get("fields") or [{}])[0].get("type", "?")
        entry: dict = {
            "label": label,
            "type": ftype,
            "required": bool(q.get("required")),
            "value": None,
            "source": None,
        }
        # Select options come from the Greenhouse schema — the model may
        # PRE-CHOOSE one (validated as an exact option in draft_answer),
        # and the filler only ever selects exact matches. The human still
        # reviews every choice in the open browser before submitting.
        values = (q.get("fields") or [{}])[0].get("values") or []
        options = [str(v.get("label", "")).strip() for v in values if v.get("label")]
        if options:
            entry["options"] = options
        if ftype == "input_file":
            if "resume" in fl or "cv" in fl:
                entry["value"], entry["source"] = resume, "wiki"
        else:
            for needle, fact in _LABEL_TO_FACT:
                if needle in fl and facts.get(fact):
                    entry["value"], entry["source"] = facts[fact], "wiki"
                    break
            else:
                if _HUMAN_ONLY_RE.search(label):
                    entry["human_only"] = True
                elif ftype in ("textarea", "input_text") or options:
                    # Every unanswered question the model can sensibly
                    # draft: essays, short texts (start date, address),
                    # and selects whose right answer the wiki knows —
                    # legal/EEO questions never reach this branch.
                    suffix = f"  [choose ONE of: {' | '.join(options)}]" if options else ""
                    needs_draft.append(label + suffix)
        fields.append(entry)

    import html as _html

    app_id = f"{org}-{job_id}"
    plan = {
        "id": app_id,
        "url": data.get("absolute_url") or url,
        "platform": "greenhouse",
        "org": org,
        "job_id": job_id,
        "title": data.get("title", "?"),
        "created": _dt.now().isoformat(timespec="seconds"),
        # JD digest rides in the plan so draft_all_answers can ground
        # tailored answers without refetching.
        "jd": _strip_html(_html.unescape(data.get("content") or ""), cap=2500),
        "fields": fields,
    }
    path = _applications_dir() / f"{app_id}.json"
    path.write_text(_json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")

    filled = sum(1 for f in fields if f["value"])
    lines = [
        f"Application plan {app_id} created for: {plan['title']}",
        f"{filled}/{len(fields)} fields pre-filled from the career wiki"
        + ("" if resume else " (NO resume PDF found in the wiki's resume/ dir)"),
    ]
    human_only = [f["label"] for f in fields if f.get("human_only")]
    if human_only:
        lines.append("Reserved for the user in the browser (never draft): "
                     + "; ".join(human_only))
    if needs_draft:
        lines.append(
            f"{len(needs_draft)} questions need drafted answers. Call "
            f"draft_all_answers(application_id={app_id!r}) — ONE call "
            "drafts them all. Use draft_answer() only to revise a "
            "single answer afterwards."
        )
    else:
        lines.append("No free-text questions; the plan is ready.")
    lines.append(
        f"When drafting is done, tell the user to run on their machine:\n"
        f"  uv run --group apply python scripts/apply_fill.py {app_id}"
    )
    return "\n".join(lines)


def draft_answer(application_id: str, question: str, answer: str) -> str:
    """Attach a drafted answer to a plan question (matched by substring)."""
    path = _applications_dir() / f"{application_id}.json"
    if not path.is_file():
        return f"ERROR: no application plan '{application_id}'. Call prepare_application(url) first."
    if not (answer or "").strip():
        return "ERROR: empty answer — draft real content grounded in career_context()."
    plan = _json.loads(path.read_text(encoding="utf-8"))
    ql = (question or "").strip().lower()
    matches = [f for f in plan["fields"] if ql and ql in f["label"].lower()]
    if len(matches) != 1:
        options = [f["label"] for f in plan["fields"] if not f["value"]]
        return (
            f"ERROR: question matched {len(matches)} fields. Unanswered fields:\n"
            + "\n".join(f"  - {o}" for o in options)
        )
    if matches[0].get("human_only"):
        return (
            "ERROR: this question (visa/legal/self-identification) is "
            "answered by the user directly in the browser — never draft it."
        )
    options = matches[0].get("options") or []
    if options:
        exact = [o for o in options if o.lower() == answer.strip().lower()]
        if not exact:
            return (
                "ERROR: this question is a choice — the answer must be "
                "EXACTLY one of:\n" + "\n".join(f"  - {o}" for o in options)
            )
        answer = exact[0]
    matches[0]["value"] = answer.strip()
    matches[0]["source"] = "model"
    path.write_text(_json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    remaining = [
        f["label"] for f in plan["fields"]
        if f["value"] is None and not f.get("human_only")
        and (f["type"] in ("textarea", "input_text") or f.get("options"))
    ]
    if remaining:
        return (
            "Saved. Still needing answers:\n"
            + "\n".join(f"  - {r}" for r in remaining)
            + "\n\nDO NOT stop or summarise yet — call draft_answer again "
            "now for the next question above. The plan is NOT complete "
            "until this list is empty."
        )
    return (
        "Saved — all free-text questions answered. Tell the user to run:\n"
        f"  uv run --group apply python scripts/apply_fill.py {application_id}"
    )


_DRAFT_SYSTEM = (
    "You draft job-application answers for the user, in their first-person "
    "voice. Ground every claim ONLY in the provided career context — never "
    "invent employers, dates, links, or numbers. If the context does not "
    "contain the fact a question needs, reply exactly UNKNOWN. Output only "
    "the answer text: no preamble, no markdown, no surrounding quotes."
)


def draft_all_answers(application_id: str) -> str:
    """Draft every open question in a plan with one tool call.

    The harness fans out one bounded plain-chat LLM call per question
    instead of relying on the agent loop to sustain a long draft_answer
    sequence (observed live: the model quits after 1-3 sequential calls
    and no prompt or correction changes that — guards force honesty,
    not stamina; iteration belongs to the harness)."""
    path = _applications_dir() / f"{application_id}.json"
    if not path.is_file():
        return f"ERROR: no application plan '{application_id}'. Call prepare_application(url) first."
    plan = _json.loads(path.read_text(encoding="utf-8"))

    open_fields = [
        f for f in plan["fields"]
        if f["value"] is None and not f.get("human_only")
        and (f["type"] in ("textarea", "input_text") or f.get("options"))
    ]
    if not open_fields:
        return (
            "Nothing to draft — every model-draftable question is answered. "
            f"Tell the user to run:\n  uv run --group apply python scripts/apply_fill.py {application_id}"
        )

    from homunculus.config import get_config
    from homunculus.llm import call_llm

    drafting_model = get_config().provider.drafting_model
    context = career_context()[:6000]
    jd = str(plan.get("jd") or "")[:2500]
    drafted: list[str] = []
    left_for_user: list[str] = []

    for f in open_fields:
        opts = f.get("options") or []
        if opts:
            instruction = "Answer with EXACTLY one of these options, nothing else: " + " | ".join(opts)
        elif f["type"] == "textarea":
            instruction = ("Write the answer: 2-6 sentences, specific to this job, "
                           "first person, grounded only in the career context.")
        else:
            instruction = "Write a short one-line answer (a few words at most)."
        messages = [
            {"role": "system", "content": _DRAFT_SYSTEM},
            {"role": "user", "content": (
                f"CAREER CONTEXT:\n{context}\n\nJOB ({plan.get('title', '?')}):\n{jd}"
                f"\n\nFORM QUESTION: {f['label']}\n{instruction}"
            )},
        ]
        try:
            answer = (
                call_llm(messages, None, model=drafting_model).get("content") or ""
            ).strip().strip('\'"')
        except Exception as e:  # noqa: BLE001 — one failed draft must not sink the batch
            log.warning(f"[career] draft failed for {f['label']!r}: {e}")
            left_for_user.append(f["label"])
            continue
        if opts:
            exact = [o for o in opts if o.lower() == answer.lower()]
            if not exact:
                messages += [
                    {"role": "assistant", "content": answer},
                    {"role": "user", "content": "Invalid. Reply with exactly one of: " + " | ".join(opts)},
                ]
                try:
                    answer = (
                        call_llm(messages, None, model=drafting_model).get("content") or ""
                    ).strip()
                except Exception:  # noqa: BLE001
                    answer = ""
                exact = [o for o in opts if o.lower() == answer.strip().lower()]
            if not exact:
                left_for_user.append(f["label"])
                continue
            answer = exact[0]
        if not answer or answer.upper().startswith("UNKNOWN"):
            # The wiki genuinely lacks the fact (e.g. "have you
            # interviewed here before?") — guessing would be worse.
            left_for_user.append(f["label"])
            continue
        f["value"], f["source"] = answer, "model"
        drafted.append(f["label"])

    path.write_text(_json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [f"Drafted {len(drafted)}/{len(open_fields)} questions:"]
    lines += [f"  ✓ {d}" for d in drafted]
    if left_for_user:
        lines.append("Left for the user (context lacks the fact, or no valid option):")
        lines += [f"  ○ {q}" for q in left_for_user]
    lines.append(
        "Tell the user to review and run:\n"
        f"  uv run --group apply python scripts/apply_fill.py {application_id}"
    )
    return "\n".join(lines)
