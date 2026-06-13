"""Schedule math for the Monday GitHub-health check.

The file-IO + TaskStore plumbing is covered elsewhere; the off-by-one
risk is the "next Monday 09:00" calc when today *is* Monday, before or
after 09:00. The script works in naive user-local wall clock
(now_user_naive), so we patch that rather than datetime.now.
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
        "bootstrap_github_health", root / "scripts" / "bootstrap_github_health.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _next_monday(now: datetime) -> datetime:
    mod = _load_script()
    with patch("user_tz.now_user_naive", return_value=now):
        return datetime.fromisoformat(mod._next_monday_9am_user_naive())


def test_saturday_advances_to_monday():
    # Sat 2026-06-13 → Mon 2026-06-15 09:00
    assert _next_monday(datetime(2026, 6, 13, 14, 0)) == datetime(2026, 6, 15, 9, 0)


def test_monday_before_9am_picks_today():
    assert _next_monday(datetime(2026, 6, 15, 7, 0)) == datetime(2026, 6, 15, 9, 0)


def test_monday_after_9am_advances_a_week():
    assert _next_monday(datetime(2026, 6, 15, 10, 0)) == datetime(2026, 6, 22, 9, 0)


def test_sunday_picks_next_day():
    # Sun 2026-06-14 → Mon 2026-06-15
    assert _next_monday(datetime(2026, 6, 14, 23, 0)) == datetime(2026, 6, 15, 9, 0)
