"""Active modifier re-injection + AGENTS.md hot-reload.

Both fix the "instruction dilution" failure mode where the original
task/persona is buried deep in context and its attention weight
drops. mem0's research quantified this; we've felt it as the
leetcode task forgetting its own success criteria at iter ~10.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch


def test_agents_md_hot_reload_picks_up_disk_edits(tmp_path, monkeypatch):
    """Edit AGENTS.md on disk → next _current_system_prompt call
    reflects the change without a restart."""
    import core
    agents_path = tmp_path / "AGENTS.md"
    agents_path.write_text("# Original persona\nbe terse", encoding="utf-8")
    monkeypatch.setenv("HOMUNCULUS_AGENTS_MD", str(agents_path))

    agent = core.Agent()
    p1 = agent._current_system_prompt()
    assert "Original persona" in p1
    assert "be terse" in p1

    # User edits the file on disk.
    import time
    time.sleep(0.02)  # ensure mtime is observably different on fast FS
    agents_path.write_text("# Updated persona\nbe chatty", encoding="utf-8")

    p2 = agent._current_system_prompt()
    assert "Updated persona" in p2
    assert "be chatty" in p2
    assert "be terse" not in p2


def test_agents_md_cached_when_mtime_unchanged(tmp_path, monkeypatch):
    """Repeated reads of an unchanged AGENTS.md hit the cache —
    we don't want disk IO on every turn for a stable file."""
    import core
    agents_path = tmp_path / "AGENTS.md"
    agents_path.write_text("# Same persona", encoding="utf-8")
    monkeypatch.setenv("HOMUNCULUS_AGENTS_MD", str(agents_path))

    agent = core.Agent()
    agent._current_system_prompt()  # populates cache

    real_read = Path.read_text
    call_count = {"n": 0}
    def counting_read(self, *a, **k):
        if self == agents_path:
            call_count["n"] += 1
        return real_read(self, *a, **k)

    with patch.object(Path, "read_text", counting_read):
        for _ in range(5):
            agent._current_system_prompt()
    assert call_count["n"] == 0, (
        f"AGENTS.md read {call_count['n']} times despite unchanged mtime; "
        "cache is broken"
    )


def test_agents_md_missing_is_silent(tmp_path, monkeypatch):
    """No AGENTS.md = no error, no identity block in the prompt."""
    import core
    monkeypatch.setenv("HOMUNCULUS_AGENTS_MD", str(tmp_path / "nope.md"))
    agent = core.Agent()
    prompt = agent._current_system_prompt()
    assert "AGENTS.md" not in prompt
    assert "Identity (AGENTS.md" not in prompt
