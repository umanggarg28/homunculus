"""Clean legacy runtime state out of skill_*.md files (one-time, idempotent).

Earlier versions tracked a skill's `uses` / `consecutive_failures` in the
file's frontmatter and appended dated `## Run log` / `## Watch outs` sections
to its body. That telemetry now lives in `_skill_stats.json`
(stores.SkillStatsStore), so the skill file can be pure authored content.

This script seeds the stats sidecar from any legacy counters (without
clobbering newer sidecar values), then rewrites each file with the runtime
frontmatter keys and runtime sections removed. rate_skill performs the same
migration lazily on each skill's next rating; run this to clean every file at
once instead of waiting.

    docker compose exec web sh -c 'cd /app && uv run python scripts/migrate_skill_stats.py'
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from homunculus.skills import migrate_skill_file  # noqa: E402
from homunculus.stores import SkillStatsStore  # noqa: E402


def main() -> None:
    memory_dir = Path(os.environ.get("HOMUNCULUS_MEMORY_DIR", "./memory"))
    if not memory_dir.exists():
        print(f"memory dir {memory_dir} does not exist — nothing to migrate")
        return

    stats = SkillStatsStore(memory_dir)
    skill_files = sorted(memory_dir.glob("skill_*.md"))
    if not skill_files:
        print(f"no skill_*.md files under {memory_dir}")
        return

    changed = 0
    for path in skill_files:
        if migrate_skill_file(path, stats):
            changed += 1
            print(f"cleaned {path.name}")
        else:
            print(f"already clean {path.name}")
    print(f"\n{changed}/{len(skill_files)} file(s) migrated; "
          f"telemetry seeded into {stats.path.name}")


if __name__ == "__main__":
    main()
