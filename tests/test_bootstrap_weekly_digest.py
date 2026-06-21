"""Schedule math for the consolidated Sunday digest task.

The skill upgrade and TaskStore plumbing are covered elsewhere; the
off-by-one risk is "next Sunday 09:00" when today is Sunday before or
after 09:00. Works in naive user-local wall clock (now_user_naive).
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


def _load_script():
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location(
        "bootstrap_weekly_digest", root / "scripts" / "bootstrap_weekly_digest.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _next_sunday(now: datetime) -> datetime:
    mod = _load_script()
    with patch("homunculus.user_tz.now_user_naive", return_value=now):
        return datetime.fromisoformat(mod._next_sunday_9am_user_naive())


def test_saturday_advances_to_sunday():
    assert _next_sunday(datetime(2026, 6, 13, 14, 0)) == datetime(2026, 6, 14, 9, 0)


def test_sunday_before_9am_picks_today():
    assert _next_sunday(datetime(2026, 6, 14, 7, 0)) == datetime(2026, 6, 14, 9, 0)


def test_sunday_after_9am_advances_a_week():
    assert _next_sunday(datetime(2026, 6, 14, 10, 0)) == datetime(2026, 6, 21, 9, 0)


def test_monday_advances_to_coming_sunday():
    assert _next_sunday(datetime(2026, 6, 15, 9, 0)) == datetime(2026, 6, 21, 9, 0)
