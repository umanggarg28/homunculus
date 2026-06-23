"""ProposalStore — the review queue between agent self-authoring and the
live skill registry.

Nothing the agent proposes goes live without passing through here as a
pending proposal that a human approves or rejects. Pin the lifecycle:
create → pending, approve/reject is terminal and one-way, ids are
stable, corrupt files recover as empty.
"""

from __future__ import annotations

import pytest

from homunculus.proposals import ProposalStore, KIND_NEW_SKILL, KIND_SKILL_EDIT


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMUNCULUS_USER_TZ_FILE", str(tmp_path / "tz.txt"))
    return ProposalStore(tmp_path / "proposals.json")


def test_create_starts_pending_with_sequential_ids(store):
    p1 = store.create(kind=KIND_SKILL_EDIT, skill_name="skill_a", body="...", rationale="r")
    p2 = store.create(kind=KIND_NEW_SKILL, skill_name="skill_b", body="...")
    assert p1["id"] == "prop-0001"
    assert p2["id"] == "prop-0002"
    assert p1["status"] == "pending"
    assert store.pending_count() == 2


def test_unknown_kind_rejected(store):
    with pytest.raises(ValueError):
        store.create(kind="mutate_everything", skill_name="skill_a", body="...")


def test_approve_is_terminal(store):
    p = store.create(kind=KIND_SKILL_EDIT, skill_name="skill_a", body="...")
    approved = store.mark_approved(p["id"], note="looks right")
    assert approved["status"] == "approved"
    assert approved["resolution_note"] == "looks right"
    assert approved["resolved_at"] is not None
    # Can't re-resolve.
    with pytest.raises(ValueError):
        store.mark_rejected(p["id"])


def test_reject_keeps_it_out_of_pending(store):
    p = store.create(kind=KIND_NEW_SKILL, skill_name="skill_b", body="...")
    store.mark_rejected(p["id"], note="not useful")
    assert store.pending_count() == 0
    assert store.list("rejected")[0]["id"] == p["id"]


def test_resolving_missing_proposal_raises(store):
    with pytest.raises(KeyError):
        store.mark_approved("prop-9999")


def test_list_filters_by_status(store):
    a = store.create(kind=KIND_SKILL_EDIT, skill_name="skill_a", body="...")
    b = store.create(kind=KIND_SKILL_EDIT, skill_name="skill_b", body="...")
    store.mark_approved(a["id"])
    assert [p["id"] for p in store.list("pending")] == [b["id"]]
    assert [p["id"] for p in store.list("approved")] == [a["id"]]
    assert len(store.list("all")) == 2


def test_task_spec_round_trips(store):
    spec = {"title": "Summarize HN", "recurrence": "weekly", "success_criteria": [{"type": "notify_called"}]}
    p = store.create(kind=KIND_NEW_SKILL, skill_name="skill_hn", body="...", task_spec=spec)
    assert store.get(p["id"])["task_spec"] == spec


def test_corrupt_file_recovers_as_empty(store):
    store.path.write_text("{not json", encoding="utf-8")
    assert store.list("all") == []
    p = store.create(kind=KIND_SKILL_EDIT, skill_name="skill_a", body="...")
    assert p["id"] == "prop-0001"


def test_ids_continue_after_load(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMUNCULUS_USER_TZ_FILE", str(tmp_path / "tz.txt"))
    s1 = ProposalStore(tmp_path / "p.json")
    s1.create(kind=KIND_SKILL_EDIT, skill_name="skill_a", body="...")
    # Fresh instance reading the same file must not reuse prop-0001.
    s2 = ProposalStore(tmp_path / "p.json")
    assert s2.create(kind=KIND_SKILL_EDIT, skill_name="skill_b", body="...")["id"] == "prop-0002"


def test_duplicate_pending_proposal_is_deduped(store):
    """A second pending proposal for the same (skill, kind) returns the
    existing one flagged, never a duplicate — one change to review, not a pile.
    A reflection tick can file several near-identical edits in one run."""
    first = store.create(kind=KIND_SKILL_EDIT, skill_name="skill_a", body="v1", rationale="r1")
    dup = store.create(kind=KIND_SKILL_EDIT, skill_name="skill_a", body="v2", rationale="r2")

    assert dup["_deduped"] is True
    assert dup["id"] == first["id"]
    # Only the first is stored; the second never persisted.
    pending = store.list("pending")
    assert len(pending) == 1
    assert pending[0]["body"] == "v1"
    assert "_deduped" not in pending[0]  # flag is transient, not persisted


def test_dedupe_is_scoped_to_skill_and_kind(store):
    """Dedupe keys on (skill, kind) — different skills, or a new_skill vs an
    edit for the same name, are independent proposals."""
    a = store.create(kind=KIND_SKILL_EDIT, skill_name="skill_a", body="...")
    b = store.create(kind=KIND_SKILL_EDIT, skill_name="skill_b", body="...")
    assert b["id"] != a["id"]
    assert not b.get("_deduped")
    assert len(store.list("pending")) == 2


def test_resolved_proposal_does_not_block_new_one(store):
    """Once the first is approved/rejected, a fresh proposal for the same
    (skill, kind) is allowed again — dedupe only guards the PENDING queue."""
    first = store.create(kind=KIND_SKILL_EDIT, skill_name="skill_a", body="v1")
    store.mark_rejected(first["id"])
    second = store.create(kind=KIND_SKILL_EDIT, skill_name="skill_a", body="v2")
    assert not second.get("_deduped")
    assert second["id"] != first["id"]
    assert [p["body"] for p in store.list("pending")] == ["v2"]
