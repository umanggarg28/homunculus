"""Skills registry — versioned procedural memory.

Storage layer only (PR 1 of 3 for skill self-refinement). PR 2 wires
the refinement-mode agent to save through this registry; PR 3 wires
auto-trigger from the reflection tick.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from homunculus.skills import Skills


# ---- names + paths ----------------------------------------------------


def test_load_returns_none_when_skill_absent(tmp_path: Path) -> None:
    s = Skills(tmp_path)
    assert s.load("skill_deliver_daily_leetcode") is None


def test_save_then_load_round_trip(tmp_path: Path) -> None:
    s = Skills(tmp_path)
    s.save("skill_x", "body v1", source="manual")
    assert s.load("skill_x") == "body v1"


def test_invalid_name_raises(tmp_path: Path) -> None:
    s = Skills(tmp_path)
    # Missing required `skill_` prefix
    with pytest.raises(ValueError, match="invalid skill name"):
        s.save("not_a_skill", "body", source="manual")
    # Disallowed chars
    with pytest.raises(ValueError, match="invalid skill name"):
        s.save("skill_With Spaces", "body", source="manual")


def test_list_all_returns_only_skill_files(tmp_path: Path) -> None:
    s = Skills(tmp_path)
    s.save("skill_a", "A", source="bootstrap")
    s.save("skill_b", "B", source="bootstrap")
    # Sibling non-skill file must not appear
    (tmp_path / "MEMORY.md").write_text("index", encoding="utf-8")
    (tmp_path / "feedback_x.md").write_text("rule", encoding="utf-8")
    assert s.list_all() == ["skill_a", "skill_b"]


# ---- versioning -------------------------------------------------------


def test_first_save_creates_version_1(tmp_path: Path) -> None:
    s = Skills(tmp_path)
    v = s.save("skill_x", "body v1", source="manual")
    assert v == 1
    assert s.current_version("skill_x") == 1


def test_second_save_creates_version_2(tmp_path: Path) -> None:
    s = Skills(tmp_path)
    s.save("skill_x", "body v1", source="manual")
    v = s.save("skill_x", "body v2", source="refinement-tick", rationale="add code block")
    assert v == 2
    assert s.current_version("skill_x") == 2
    assert s.load("skill_x") == "body v2"


def test_version_metadata_captured(tmp_path: Path) -> None:
    s = Skills(tmp_path)
    s.save("skill_x", "v1", source="bootstrap")
    s.save("skill_x", "v2", source="refinement-tick", rationale="switch to graphql")
    versions = s.versions("skill_x")
    assert len(versions) == 2
    assert versions[0]["version"] == 1
    assert versions[0]["source"] == "bootstrap"
    assert versions[0]["prior_version"] is None
    assert versions[1]["version"] == 2
    assert versions[1]["source"] == "refinement-tick"
    assert versions[1]["rationale"] == "switch to graphql"
    assert versions[1]["prior_version"] == 1


def test_get_version_body_retrieves_prior(tmp_path: Path) -> None:
    s = Skills(tmp_path)
    s.save("skill_x", "v1 body content here", source="bootstrap")
    s.save("skill_x", "v2 body content here", source="refinement-tick")
    assert s.get_version_body("skill_x", 1) == "v1 body content here"
    assert s.get_version_body("skill_x", 2) == "v2 body content here"


def test_get_version_body_unknown_version_returns_none(tmp_path: Path) -> None:
    s = Skills(tmp_path)
    s.save("skill_x", "v1", source="bootstrap")
    assert s.get_version_body("skill_x", 99) is None


# ---- revert -----------------------------------------------------------


def test_revert_to_restores_prior_body(tmp_path: Path) -> None:
    s = Skills(tmp_path)
    s.save("skill_x", "good v1", source="bootstrap")
    s.save("skill_x", "bad v2", source="refinement-tick")
    s.revert_to("skill_x", 1, rationale="v2 caused 4xx errors")
    assert s.load("skill_x") == "good v1"


def test_revert_records_as_new_version(tmp_path: Path) -> None:
    """The revert itself should be a new version (v3), not silently
    set the head back to v1 — so the history shows what happened."""
    s = Skills(tmp_path)
    s.save("skill_x", "v1", source="bootstrap")
    s.save("skill_x", "v2", source="refinement-tick")
    s.revert_to("skill_x", 1)
    assert s.current_version("skill_x") == 3
    versions = s.versions("skill_x")
    assert versions[-1]["source"] == "user-edit"
    assert "revert" in versions[-1]["rationale"].lower()


def test_revert_to_unknown_version_raises(tmp_path: Path) -> None:
    s = Skills(tmp_path)
    s.save("skill_x", "v1", source="bootstrap")
    with pytest.raises(ValueError, match="no archived body"):
        s.revert_to("skill_x", 99)


# ---- atomicity --------------------------------------------------------


def test_save_does_not_clobber_on_disk_atomically(tmp_path: Path) -> None:
    """save() uses .tmp + replace; the canonical file is never partial.
    Sanity check: after a save the file content matches the body exactly,
    not a truncated prefix."""
    s = Skills(tmp_path)
    big_body = "x" * 50_000
    s.save("skill_x", big_body, source="manual")
    assert s.load("skill_x") == big_body
    assert len(s.load("skill_x") or "") == 50_000


def test_concurrent_saves_serialize(tmp_path: Path) -> None:
    """Two threads saving same skill must not produce duplicate versions
    or corrupt the manifest."""
    s = Skills(tmp_path)
    s.save("skill_x", "v1", source="bootstrap")

    barrier = threading.Barrier(4)
    versions: list[int] = []
    lock = threading.Lock()

    def saver(idx: int) -> None:
        local = Skills(tmp_path)  # simulate independent process
        barrier.wait()
        v = local.save("skill_x", f"body from thread {idx}", source="refinement-tick")
        with lock:
            versions.append(v)

    threads = [threading.Thread(target=saver, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(versions) == [2, 3, 4, 5], (
        f"expected unique sequential versions after 4 saves on v1, got {versions}"
    )
    # Manifest matches: current = 5, history has 5 entries.
    assert Skills(tmp_path).current_version("skill_x") == 5
    assert len(Skills(tmp_path).versions("skill_x")) == 5


# ---- manifest shape ---------------------------------------------------


def test_manifest_is_human_readable_json(tmp_path: Path) -> None:
    """Manifest is meant to be inspected manually too. Indented JSON,
    no surprises."""
    s = Skills(tmp_path)
    s.save("skill_x", "v1", source="bootstrap", rationale="initial")
    manifest_path = tmp_path / ".skill_history" / "skill_x" / "_manifest.json"
    assert manifest_path.exists()
    raw = manifest_path.read_text(encoding="utf-8")
    assert "\n  " in raw, "manifest should be pretty-printed"
    data = json.loads(raw)
    assert data["latest_version"] == 1
    assert data["versions"][0]["rationale"] == "initial"


def test_exists_reflects_canonical_file(tmp_path: Path) -> None:
    s = Skills(tmp_path)
    assert not s.exists("skill_x")
    s.save("skill_x", "v1", source="bootstrap")
    assert s.exists("skill_x")


def test_save_with_no_history_works(tmp_path: Path) -> None:
    """First-ever save for a brand-new skill has no prior to archive —
    must not crash."""
    s = Skills(tmp_path)
    v = s.save("skill_brand_new", "fresh body", source="bootstrap")
    assert v == 1
    versions = s.versions("skill_brand_new")
    assert versions[0]["prior_version"] is None
