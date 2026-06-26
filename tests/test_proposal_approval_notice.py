"""The heartbeat surfaces newly-filed proposals as a structured approval notice.

This is the harness-owned replacement for the agent narrating its own pending
changes: after an autonomous tick, any proposal filed this tick is announced
once, by id, pointing the user at Overview (and the chat approve/reject command).
"""

from __future__ import annotations

import pytest

from homunculus import heartbeat
from homunculus.proposals import KIND_MEMORY_DELETE, KIND_SKILL_EDIT, ProposalStore


@pytest.fixture()
def store(tmp_path, monkeypatch):
    path = tmp_path / "proposals.json"
    monkeypatch.setenv("HOMUNCULUS_PROPOSALS_FILE", str(path))
    return ProposalStore(path)


def test_format_notice_has_label_target_id_and_review_hint():
    notice = heartbeat._format_approval_notice({
        "id": "prop-0021", "kind": "skill_edit",
        "skill_name": "skill_hn_ai_summary",
        "rationale": "add web_search fallback\nsecond line ignored",
    })
    assert "EDIT SKILL" in notice
    assert "skill_hn_ai_summary" in notice
    assert "add web_search fallback" in notice
    assert "second line ignored" not in notice  # only the first rationale line
    assert "approve prop-0021" in notice and "reject prop-0021" in notice


def test_notify_only_proposals_filed_since_snapshot(store, monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(heartbeat, "deliver", lambda text: sent.append(text))

    old = store.create(kind=KIND_SKILL_EDIT, skill_name="skill_a", body="x")
    before = heartbeat._pending_proposal_ids()  # snapshot includes `old`
    new = store.create(
        kind=KIND_MEMORY_DELETE, skill_name="project_b.md", body="",
        validation={"target": "project_b.md"},
    )

    heartbeat._notify_new_proposals(before)

    assert len(sent) == 1, "exactly one notice — only the new proposal"
    assert new["id"] in sent[0]
    assert old["id"] not in sent[0]


def test_notify_nothing_when_no_new_proposals(store, monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(heartbeat, "deliver", lambda text: sent.append(text))
    store.create(kind=KIND_SKILL_EDIT, skill_name="skill_a", body="x")
    before = heartbeat._pending_proposal_ids()

    heartbeat._notify_new_proposals(before)

    assert sent == []


def test_delivery_error_never_propagates(store, monkeypatch):
    def boom(_text):
        raise RuntimeError("channel down")

    monkeypatch.setattr(heartbeat, "deliver", boom)
    store.create(kind=KIND_SKILL_EDIT, skill_name="skill_a", body="x")

    # Must not raise — a notify failure can never break a tick.
    heartbeat._notify_new_proposals(set())
