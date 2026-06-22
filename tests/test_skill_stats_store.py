"""Skill execution telemetry lives in a sidecar, not in the skill file.

Covers stores.SkillStatsStore, the legacy-file migration (counters out of
frontmatter, Run log / Watch outs out of the body), and that rate_skill +
load_skill_playbook keep the authored skill file clean.
"""

from __future__ import annotations

from homunculus.memory import Memory
from homunculus.skills import (
    _strip_runtime_body_sections,
    _strip_runtime_frontmatter,
    load_skill_playbook,
    migrate_skill_file,
)
from homunculus.stores import SkillStatsStore


# ---- SkillStatsStore --------------------------------------------------------

def test_record_increments_uses_and_failure_streak(tmp_path):
    store = SkillStatsStore(tmp_path)
    assert store.get("skill_x") == {}

    s = store.record("skill_x", "success")
    assert s["uses"] == 1
    assert s["consecutive_failures"] == 0

    s = store.record("skill_x", "failure")
    assert s["uses"] == 2
    assert s["consecutive_failures"] == 1

    s = store.record("skill_x", "failure")
    assert s["consecutive_failures"] == 2

    s = store.record("skill_x", "success")
    assert s["uses"] == 4
    assert s["consecutive_failures"] == 0


def test_record_rejects_unknown_outcome(tmp_path):
    store = SkillStatsStore(tmp_path)
    store.record("skill_x", "success")
    before = store.get("skill_x")
    after = store.record("skill_x", "maybe")
    assert after == before  # unchanged


def test_run_log_is_capped(tmp_path):
    store = SkillStatsStore(tmp_path)
    for _ in range(30):
        store.record("skill_x", "success")
    s = store.get("skill_x")
    assert s["uses"] == 30
    assert len(s["run_log"]) == 20  # _MAX_RUN_LOG tail only


def test_seed_if_absent_does_not_clobber(tmp_path):
    store = SkillStatsStore(tmp_path)
    store.record("skill_x", "failure")  # consec = 1
    store.seed_if_absent("skill_x", uses=99, consecutive_failures=99)
    assert store.get("skill_x")["uses"] == 1  # seed skipped — entry exists

    store.seed_if_absent("skill_y", uses=7, consecutive_failures=2)
    assert store.get("skill_y")["uses"] == 7


# ---- pure stripping helpers -------------------------------------------------

def test_strip_runtime_body_sections_removes_run_log_and_watch_outs():
    body = (
        "# Skill\n\n1. Do the thing.\n\n# Rationale\nBecause.\n\n"
        "## Run log\n- 2026-06-20 · success\n\n"
        "## Watch outs\n* Watch out (2026-06-21): something\n"
    )
    out = _strip_runtime_body_sections(body)
    assert "Run log" not in out
    assert "Watch out" not in out
    assert "Do the thing." in out
    assert "Because." in out


def test_strip_runtime_body_sections_keeps_authored_h2():
    body = "# Skill\n\n## Steps\n1. go\n\n## Run log\n- x · success\n"
    out = _strip_runtime_body_sections(body)
    assert "## Steps" in out
    assert "Run log" not in out


def test_strip_runtime_frontmatter_preserves_authored_yaml():
    text = (
        "---\nconsecutive_failures: 0\nuses: 7\nname: skill_x\n"
        "description: A skill\nrequires_tools:\n  - get_weather\n---\nBody.\n"
    )
    out = _strip_runtime_frontmatter(text)
    assert "uses:" not in out
    assert "consecutive_failures:" not in out
    # Authored frontmatter preserved verbatim.
    assert "name: skill_x" in out
    assert "description: A skill" in out
    assert "  - get_weather" in out
    assert out.endswith("---\nBody.\n")


# ---- migration --------------------------------------------------------------

def test_migrate_skill_file_seeds_stats_and_cleans_file(tmp_path):
    path = tmp_path / "skill_brief.md"
    path.write_text(
        "---\nconsecutive_failures: 2\nuses: 7\nname: skill_brief\n"
        "description: Morning brief\n---\n"
        "# Skill\n1. step\n\n# Rationale\nbecause\n\n"
        "## Run log\n- 2026-06-20 · success\n\n"
        "## Watch outs\n* Watch out (2026-06-21): be careful\n",
        encoding="utf-8",
    )
    store = SkillStatsStore(tmp_path)

    changed = migrate_skill_file(path, store)
    assert changed is True

    # Counters seeded into the sidecar.
    s = store.get("skill_brief")
    assert s["uses"] == 7
    assert s["consecutive_failures"] == 2

    # File is now clean authored content.
    text = path.read_text(encoding="utf-8")
    assert "uses:" not in text
    assert "consecutive_failures:" not in text
    assert "Run log" not in text
    assert "Watch out" not in text
    assert "name: skill_brief" in text
    assert "# Rationale" in text

    # Idempotent — second run is a no-op.
    assert migrate_skill_file(path, store) is False


def test_migrate_is_noop_on_clean_file(tmp_path):
    path = tmp_path / "skill_clean.md"
    path.write_text(
        "---\nname: skill_clean\ndescription: x\n---\n# Skill\n1. go\n",
        encoding="utf-8",
    )
    store = SkillStatsStore(tmp_path)
    assert migrate_skill_file(path, store) is False
    assert store.get("skill_clean") == {}


# ---- Memory.rate_skill end to end ------------------------------------------

def test_rate_skill_writes_sidecar_not_file(tmp_path):
    mem = Memory(tmp_path)
    skill = tmp_path / "skill_demo.md"
    skill.write_text(
        "---\nname: skill_demo\ndescription: demo\n---\n# Skill\n1. go\n",
        encoding="utf-8",
    )

    msg = mem.rate_skill("demo", "success")
    assert "uses=1" in msg

    # The file is untouched by telemetry.
    text = skill.read_text(encoding="utf-8")
    assert "uses:" not in text
    assert "Run log" not in text

    # The sidecar holds the count.
    assert mem.skill_stats.get("skill_demo")["uses"] == 1


def test_rate_skill_migrates_legacy_count(tmp_path):
    mem = Memory(tmp_path)
    skill = tmp_path / "skill_legacy.md"
    skill.write_text(
        "---\nuses: 4\nconsecutive_failures: 0\nname: skill_legacy\n"
        "description: d\n---\n# Skill\n1. go\n\n## Run log\n- x · success\n",
        encoding="utf-8",
    )

    msg = mem.rate_skill("legacy", "success")
    # Legacy count carried over (4) then incremented → 5.
    assert "uses=5" in msg
    text = skill.read_text(encoding="utf-8")
    assert "uses:" not in text
    assert "Run log" not in text


# ---- load_skill_playbook keeps the injected body clean ----------------------

def test_load_skill_playbook_strips_runtime_sections(tmp_path):
    skill = tmp_path / "skill_inj.md"
    skill.write_text(
        "---\nname: skill_inj\ndescription: d\n---\n"
        "# Skill\n1. go\n\n## Run log\n- x · success\n\n"
        "## Watch outs\n* Watch out (2026-06-21): careful\n",
        encoding="utf-8",
    )
    states, body = load_skill_playbook(tmp_path, "skill_inj")
    assert states is None
    assert "go" in body
    assert "Run log" not in body
    assert "Watch out" not in body
