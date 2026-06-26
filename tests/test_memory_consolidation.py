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
