"""Item 7 of the robustness plan — task-intake clarifier.

Under-specified tasks like "remind me about jobs" silently fail at the
first heartbeat run because the agent can't infer when to fire. The
clarifier refuses to save such tasks at intake time and asks for the
missing information.

Heuristic: a task is "vague" when all of:
  - No `due_at`
  - No recurrence (or recurrence == "none")
  - Title + description contain no temporal marker

We test each predicate independently and confirm vague tasks are blocked
while well-specified tasks pass through to storage.
"""

import importlib.util
from pathlib import Path

# conftest stubs `tools` as a flat module so `from tools._intake import ...`
# silently returns nothing. Load _intake.py directly — it has no package-
# relative imports so it loads cleanly on its own.
_intake_path = Path(__file__).parent.parent / "tools" / "_intake.py"
_spec = importlib.util.spec_from_file_location("intake_real", _intake_path)
assert _spec and _spec.loader
_intake = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_intake)
_has_temporal_marker = _intake.has_temporal_marker
_needs_clarification = _intake.needs_clarification


# ── _has_temporal_marker ─────────────────────────────────────────────


def test_marker_detects_am_pm():
    assert _has_temporal_marker("remind me to apply for jobs at 9am")
    assert _has_temporal_marker("call mom at 7 pm")


def test_marker_detects_day_names():
    assert _has_temporal_marker("review code on Monday")
    assert _has_temporal_marker("plan for tomorrow morning")


def test_marker_detects_recurrence_words():
    assert _has_temporal_marker("send reminder daily")
    assert _has_temporal_marker("weekly status update")


def test_marker_misses_truly_vague_text():
    assert not _has_temporal_marker("remind me about jobs")
    assert not _has_temporal_marker("look into the data")


# ── _needs_clarification ─────────────────────────────────────────────


def test_explicit_due_at_passes_through():
    """A precise due_at makes everything else moot."""
    assert _needs_clarification(
        title="vague title",
        description="",
        due_at="2026-06-10T09:00:00",
        recurrence="none",
    ) is None


def test_recurrence_supplies_implicit_cadence():
    """Daily / weekly tasks don't need a due_at."""
    assert _needs_clarification(
        title="apply for jobs",
        description="",
        due_at=None,
        recurrence="daily",
    ) is None


def test_temporal_marker_in_title_passes():
    """Natural-language time in the title is enough."""
    assert _needs_clarification(
        title="remind me at 9am tomorrow",
        description="",
        due_at=None,
        recurrence="none",
    ) is None


def test_truly_vague_task_is_blocked():
    """The exact pattern from the user's earlier 'apply for jobs' shape —
    no due, no recurrence, no temporal hint anywhere."""
    result = _needs_clarification(
        title="apply for jobs",
        description="",
        due_at=None,
        recurrence="none",
    )
    assert result is not None
    assert "NEEDS_CLARIFICATION" in result
    # The message tells the agent what to ask the user
    assert "when" in result.lower()
    assert "tomorrow" in result.lower() or "daily" in result.lower()  # examples present


def test_vague_title_with_descriptive_temporal_description_passes():
    """If the description provides the time, that's enough — clarifier
    looks at title + description together."""
    assert _needs_clarification(
        title="check in",
        description="every morning at 8am",
        due_at=None,
        recurrence="none",
    ) is None
