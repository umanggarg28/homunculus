"""_normalize_datetime must land in the USER's timezone, not the container's.

Live failure 2026-06-11: the user asked for a reminder at 12:00 PM IST.
The agent correctly passed "2026-06-11T12:00:00+05:30", but
_normalize_datetime called astimezone() with no argument — container
local, UTC in Docker — storing 06:30. The reminder fired 5.5 hours
early. The store's canonical form is naive user-TZ wall clock
(everything else uses now_user_naive), so tz-aware input must convert
to the user's zone before dropping tzinfo.
"""

import importlib.util
from pathlib import Path

import pytest


def _real_tasks_module():
    spec = importlib.util.spec_from_file_location(
        "tasks_real_tz_test", Path(__file__).parent.parent / "tasks.py"
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture
def ist_user(tmp_path, monkeypatch):
    tz_file = tmp_path / "user_tz.txt"
    tz_file.write_text("Asia/Kolkata", encoding="utf-8")
    monkeypatch.setenv("HOMUNCULUS_USER_TZ_FILE", str(tz_file))
    import user_tz
    user_tz._cached_name = None
    user_tz._cached_mtime = None
    yield
    user_tz._cached_name = None
    user_tz._cached_mtime = None


def test_aware_ist_input_kept_as_ist_wall_clock(ist_user):
    norm = _real_tasks_module().TaskStore._normalize_datetime
    assert norm("2026-06-11T12:00:00+05:30") == "2026-06-11T12:00:00"


def test_aware_utc_input_converted_to_ist_wall_clock(ist_user):
    norm = _real_tasks_module().TaskStore._normalize_datetime
    # 06:30 UTC == 12:00 IST
    assert norm("2026-06-11T06:30:00+00:00") == "2026-06-11T12:00:00"


def test_naive_input_passes_through_unchanged(ist_user):
    norm = _real_tasks_module().TaskStore._normalize_datetime
    assert norm("2026-06-11T12:00:00") == "2026-06-11T12:00:00"
    assert norm(None) is None
