"""A newly-filed proposal is surfaced to the user the moment it's created.

The notice fires from the creation funnels (propose_skill / propose_consolidation)
via approvals.announce_proposal, so it is path-independent — an autonomous tick,
the Memory-page scan button, and a chat "teach it a skill" all surface the same
way. The agent never narrates this itself; it's harness-owned and structured.
"""

from __future__ import annotations

import os
import time

import homunculus.tools.notify as notify_mod
from homunculus import approvals


def test_format_notice_has_label_target_id_and_review_hint():
    notice = approvals.format_approval_notice({
        "id": "prop-0021", "kind": "skill_edit",
        "skill_name": "skill_hn_ai_summary",
        "rationale": "add web_search fallback\nsecond line ignored",
    })
    assert "EDIT SKILL" in notice
    assert "skill_hn_ai_summary" in notice
    assert "add web_search fallback" in notice
    assert "second line ignored" not in notice  # only the first rationale line
    assert "approve prop-0021" in notice and "reject prop-0021" in notice


def test_announce_delivers_for_a_new_proposal(monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(notify_mod, "deliver", lambda text: sent.append(text))

    approvals.announce_proposal({
        "id": "prop-1", "kind": "memory_delete",
        "skill_name": "project_x.md", "rationale": "duplicate",
    })

    assert len(sent) == 1 and "prop-1" in sent[0]


def test_announce_skips_a_deduped_refile(monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(notify_mod, "deliver", lambda text: sent.append(text))

    approvals.announce_proposal({"id": "prop-1", "kind": "skill_edit", "_deduped": True})

    assert sent == []  # already pending — don't ping twice


def test_announce_is_best_effort(monkeypatch):
    def boom(_text):
        raise RuntimeError("channel down")

    monkeypatch.setattr(notify_mod, "deliver", boom)
    # Must not raise — a delivery failure can never fail proposal creation.
    approvals.announce_proposal({"id": "prop-1", "kind": "skill_edit", "skill_name": "x"})


def test_scan_funnel_announces_each_filed_proposal(tmp_path, monkeypatch):
    """The Memory-page scan path (propose_consolidation) surfaces every proposal
    it files — the gap that left a manual scan silent before."""
    sent: list[str] = []
    monkeypatch.setattr(notify_mod, "deliver", lambda text: sent.append(text))

    mem = tmp_path / "memory"
    mem.mkdir()
    body = "weather task health top links grounded citations digest morning brief\n"
    for name in ("project_a.md", "project_b.md"):
        (mem / name).write_text(
            f"---\nname: {name}\ndescription: d\ntype: project\n---\n\n{body}", encoding="utf-8")
    os.utime(mem / "project_a.md", (time.time() - 10, time.time() - 10))  # older → proposed for delete

    from homunculus.memory_consolidation import propose_consolidation
    created = propose_consolidation(memory_root=mem, proposals_path=tmp_path / "proposals.json")

    assert len(created) >= 1
    assert len(sent) == len(created)  # one notice per filed proposal
    assert all("Approval needed" in s for s in sent)
