"""Deterministic memory hygiene proposals.

This is deliberately cheap: no embeddings call, no extra LLM call. It scans the
markdown vault for obvious duplicate/stale entries and files human-gated
proposals. Reflection can call it during the existing daily tick, but the scan
itself is pure filesystem work so it preserves the $5/month budget envelope.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from homunculus.approvals import announce_proposal
from homunculus.proposals import KIND_MEMORY_DELETE, ProposalStore


STOPWORDS = {
    "the", "and", "for", "that", "this", "with", "from", "your", "you",
    "are", "was", "were", "have", "has", "had", "but", "not", "all",
    "into", "about", "when", "then", "than", "they", "them", "will",
}


@dataclass(frozen=True)
class MemoryDoc:
    filename: str
    name: str
    description: str
    type: str
    body: str
    mtime: float


def load_docs(memory_root: Path) -> list[MemoryDoc]:
    docs: list[MemoryDoc] = []
    for path in sorted(memory_root.glob("*.md")):
        if path.name in {"MEMORY.md", "README.md"} or path.name.startswith("_"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        meta, body = _split_frontmatter(text)
        doc_type = meta.get("type", "")
        if doc_type == "skill":
            continue
        docs.append(MemoryDoc(
            filename=path.name,
            name=meta.get("name", path.stem),
            description=meta.get("description", ""),
            type=doc_type,
            body=body.strip(),
            mtime=path.stat().st_mtime,
        ))
    return docs


def propose_consolidation(
    *,
    memory_root: Path,
    proposals_path: Path,
    stale_days: int = 180,
    similarity_threshold: float = 0.72,
    limit: int = 5,
) -> list[dict]:
    """File memory_delete proposals for obvious hygiene candidates.

    Duplicate handling keeps the newest file and proposes deleting the older
    one. Staleness only applies to project memories, which are intentionally
    fast-decaying operational context. All changes require approval.
    """
    store = ProposalStore(proposals_path)
    docs = load_docs(memory_root)
    created: list[dict] = []

    for old, new, score in _duplicate_pairs(docs, similarity_threshold):
        if len(created) >= limit:
            break
        rationale = (
            f"Possible duplicate of {new.filename} (token overlap {score:.0%}). "
            "Review both files before approving; approval deletes only this older entry."
        )
        p = store.create(
            kind=KIND_MEMORY_DELETE,
            skill_name=old.filename,
            body="",
            rationale=rationale,
            source="memory-consolidation",
            validation={"target": old.filename, "reason": "near_duplicate", "other": new.filename},
        )
        if not p.get("_deduped"):
            announce_proposal(p)
            created.append(p)

    cutoff = time.time() - stale_days * 86400
    for doc in sorted(docs, key=lambda d: d.mtime):
        if len(created) >= limit:
            break
        if doc.type != "project" or doc.mtime >= cutoff:
            continue
        age = max(0, int((time.time() - doc.mtime) / 86400))
        p = store.create(
            kind=KIND_MEMORY_DELETE,
            skill_name=doc.filename,
            body="",
            rationale=(
                f"Project memory is {age} days old. Project memories decay quickly; "
                "approve only if this context is no longer active."
            ),
            source="memory-consolidation",
            validation={"target": doc.filename, "reason": "stale_project", "age_days": age},
        )
        if not p.get("_deduped"):
            announce_proposal(p)
            created.append(p)

    return created


def _duplicate_pairs(docs: list[MemoryDoc], threshold: float) -> list[tuple[MemoryDoc, MemoryDoc, float]]:
    out: list[tuple[MemoryDoc, MemoryDoc, float]] = []
    for i, a in enumerate(docs):
        terms_a = _terms(a)
        if len(terms_a) < 6:
            continue
        for b in docs[i + 1:]:
            if a.type != b.type:
                continue
            terms_b = _terms(b)
            if len(terms_b) < 6:
                continue
            score = len(terms_a & terms_b) / max(1, len(terms_a | terms_b))
            if score >= threshold:
                old, new = (a, b) if a.mtime <= b.mtime else (b, a)
                out.append((old, new, score))
    out.sort(key=lambda row: row[2], reverse=True)
    return out


def _terms(doc: MemoryDoc) -> set[str]:
    text = f"{doc.name}\n{doc.description}\n{doc.body}".lower()
    return {
        w for w in re.findall(r"[a-z0-9_]{3,}", text)
        if w not in STOPWORDS and not w.isdigit()
    }


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    raw = text[4:end]
    meta: dict[str, str] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        meta[k.strip()] = v.strip().strip('"')
    return meta, text[end + 5:]


def proposals_json(proposals: list[dict]) -> str:
    return json.dumps([
        {
            "id": p["id"],
            "kind": p["kind"],
            "target": p["skill_name"],
            "rationale": p["rationale"],
            "status": p["status"],
        }
        for p in proposals
    ], indent=2)
