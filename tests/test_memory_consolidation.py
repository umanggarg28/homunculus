from __future__ import annotations

import os
import time

from homunculus.memory_consolidation import propose_consolidation


def _mem(root, filename, name, typ, body):
    path = root / filename
    path.write_text(
        f"---\nname: {name}\ndescription: {name} description\ntype: {typ}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return path


def test_proposes_delete_for_near_duplicate_older_memory(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMUNCULUS_USER_TZ_FILE", str(tmp_path / "tz.txt"))
    memory = tmp_path / "memory"
    memory.mkdir()
    old = _mem(
        memory,
        "project_digest_old.md",
        "Digest old",
        "project",
        "Daily digest should include weather, task health, top links, grounded citations.",
    )
    new = _mem(
        memory,
        "project_digest_new.md",
        "Digest new",
        "project",
        "Daily digest should include weather, task health, top links, grounded citations.",
    )
    os.utime(old, (time.time() - 10, time.time() - 10))
    os.utime(new, None)

    proposals = propose_consolidation(
        memory_root=memory,
        proposals_path=tmp_path / "proposals.json",
        similarity_threshold=0.5,
    )

    assert len(proposals) == 1
    assert proposals[0]["kind"] == "memory_delete"
    assert proposals[0]["skill_name"] == "project_digest_old.md"
    assert proposals[0]["validation"]["other"] == "project_digest_new.md"


def test_proposes_stale_project_but_not_user_memory(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMUNCULUS_USER_TZ_FILE", str(tmp_path / "tz.txt"))
    memory = tmp_path / "memory"
    memory.mkdir()
    project = _mem(memory, "project_old.md", "Old project", "project", "Old project context.")
    user = _mem(memory, "user_old.md", "Old user", "user", "Old user context.")
    stale = time.time() - 200 * 86400
    os.utime(project, (stale, stale))
    os.utime(user, (stale, stale))

    proposals = propose_consolidation(
        memory_root=memory,
        proposals_path=tmp_path / "proposals.json",
        stale_days=180,
    )

    assert [p["skill_name"] for p in proposals] == ["project_old.md"]


def test_consolidation_dedupes_pending_proposals(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMUNCULUS_USER_TZ_FILE", str(tmp_path / "tz.txt"))
    memory = tmp_path / "memory"
    memory.mkdir()
    old = _mem(memory, "project_a.md", "A", "project", "Same duplicate content with enough shared terms.")
    new = _mem(memory, "project_b.md", "B", "project", "Same duplicate content with enough shared terms.")
    os.utime(old, (time.time() - 10, time.time() - 10))
    os.utime(new, None)
    path = tmp_path / "proposals.json"

    first = propose_consolidation(memory_root=memory, proposals_path=path, similarity_threshold=0.3)
    second = propose_consolidation(memory_root=memory, proposals_path=path, similarity_threshold=0.3)

    assert len(first) == 1
    assert second == []


def test_dated_series_keeps_newest_proposes_rest(tmp_path, monkeypatch):
    """A daily-log diary (same name shape, different date) can't be caught
    by the pairwise duplicate rule — every entry differs by exactly its
    date. The series rule keeps the newest and proposes the older ones."""
    monkeypatch.setenv("HOMUNCULUS_USER_TZ_FILE", str(tmp_path / "tz.txt"))
    memory = tmp_path / "memory"
    memory.mkdir()
    now = time.time()
    for i, day in enumerate(["2026-06-28", "2026-06-30", "2026-07-02"]):
        p = _mem(
            memory,
            f"feedback_{day}_log.md",
            f"log {day}",
            "feedback",
            f"Log summary for {day}: deliveries fine.",
        )
        os.utime(p, (now - (3 - i) * 86400, now - (3 - i) * 86400))
    # An unrelated dated pair (only 2 members) must NOT be flagged.
    _mem(memory, "project_release_2026-05-01.md", "rel a", "project", "release notes a")
    _mem(memory, "project_release_2026-06-01.md", "rel b", "project", "release notes b")

    proposals = propose_consolidation(
        memory_root=memory,
        proposals_path=tmp_path / "proposals.json",
    )

    targets = sorted(p["skill_name"] for p in proposals if p["validation"]["reason"] == "dated_series")
    assert targets == ["feedback_2026-06-28_log.md", "feedback_2026-06-30_log.md"]
    for p in proposals:
        if p["validation"]["reason"] == "dated_series":
            assert p["validation"]["newest"] == "feedback_2026-07-02_log.md"


def test_dated_series_respects_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMUNCULUS_USER_TZ_FILE", str(tmp_path / "tz.txt"))
    memory = tmp_path / "memory"
    memory.mkdir()
    now = time.time()
    for i in range(6):
        p = _mem(
            memory,
            f"feedback_2026-06-{10 + i:02d}_log.md",
            f"log {i}",
            "feedback",
            f"Log summary number {i}.",
        )
        os.utime(p, (now - (6 - i) * 86400, now - (6 - i) * 86400))

    proposals = propose_consolidation(
        memory_root=memory,
        proposals_path=tmp_path / "proposals.json",
        limit=2,
    )
    assert len(proposals) == 2


def test_dated_series_matches_slugified_underscore_dates(tmp_path, monkeypatch):
    """Filenames on disk come from slugify, which turns the date's dashes
    into underscores (feedback_2026_06_23_log.md) — the live vault's
    diary was invisible to a dash-only date pattern."""
    monkeypatch.setenv("HOMUNCULUS_USER_TZ_FILE", str(tmp_path / "tz.txt"))
    memory = tmp_path / "memory"
    memory.mkdir()
    now = time.time()
    for i, day in enumerate(["2026_06_23", "2026_06_24", "2026_06_25"]):
        p = _mem(
            memory,
            f"feedback_{day}_log.md",
            f"log {day}",
            "feedback",
            f"Log summary for {day}.",
        )
        os.utime(p, (now - (3 - i) * 86400, now - (3 - i) * 86400))

    proposals = propose_consolidation(
        memory_root=memory,
        proposals_path=tmp_path / "proposals.json",
    )
    targets = sorted(p["skill_name"] for p in proposals if p["validation"]["reason"] == "dated_series")
    assert targets == ["feedback_2026_06_23_log.md", "feedback_2026_06_24_log.md"]
