"""Focused tests for the weekly-nudge schedule math.

The script itself is mostly file-IO + TaskStore plumbing already
covered by tasks.py tests. The one piece worth pinning is the
"next Sunday 09:00 in user TZ" calculation — easy to off-by-one
when today *is* Sunday or after 09:00 on a Sunday.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo


def _load_script():
    """Import the bootstrap script as a top-level module so its `from
    user_tz import …` path works without a package context."""
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location(
        "bootstrap_weekly_nudge",
        root / "scripts" / "bootstrap_weekly_nudge.py",
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    sys.modules["bootstrap_weekly_nudge"] = mod
    return mod


def _parse(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def test_friday_advances_to_this_sunday():
    mod = _load_script()
    # Friday 2026-06-05 14:00 UTC → next Sunday is 2026-06-07 09:00 UTC
    fake_now = datetime(2026, 6, 5, 14, 0, tzinfo=ZoneInfo("UTC"))
    with patch.dict(os.environ, {"TZ": "UTC"}, clear=False), \
         patch.object(mod, "_user_tz_name", return_value="UTC"), \
         patch("bootstrap_weekly_nudge.datetime") as dt:
        dt.now.return_value = fake_now
        dt.side_effect = lambda *a, **k: datetime(*a, **k)
        out = _parse(mod._next_sunday_9am_in_user_tz())
    assert out == datetime(2026, 6, 7, 9, 0)


def test_sunday_before_9am_picks_today():
    mod = _load_script()
    # Sunday 2026-06-07 07:00 UTC — target later today
    fake_now = datetime(2026, 6, 7, 7, 0, tzinfo=ZoneInfo("UTC"))
    with patch.object(mod, "_user_tz_name", return_value="UTC"), \
         patch("bootstrap_weekly_nudge.datetime") as dt:
        dt.now.return_value = fake_now
        dt.side_effect = lambda *a, **k: datetime(*a, **k)
        out = _parse(mod._next_sunday_9am_in_user_tz())
    assert out == datetime(2026, 6, 7, 9, 0)


def test_sunday_after_9am_advances_a_week():
    mod = _load_script()
    # Sunday 2026-06-07 10:00 UTC — target next Sunday 2026-06-14 09:00
    fake_now = datetime(2026, 6, 7, 10, 0, tzinfo=ZoneInfo("UTC"))
    with patch.object(mod, "_user_tz_name", return_value="UTC"), \
         patch("bootstrap_weekly_nudge.datetime") as dt:
        dt.now.return_value = fake_now
        dt.side_effect = lambda *a, **k: datetime(*a, **k)
        out = _parse(mod._next_sunday_9am_in_user_tz())
    assert out == datetime(2026, 6, 14, 9, 0)


def test_tz_offset_is_respected():
    """When user is in Asia/Kolkata (UTC+5:30), "Sunday 09:00 local"
    serialises to "Sunday 03:30 UTC". The script returns naive UTC."""
    mod = _load_script()
    # Friday 2026-06-05 12:00 IST (= 06:30 UTC) → next Sun 09:00 IST = 03:30 UTC
    fake_now = datetime(2026, 6, 5, 12, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    with patch.object(mod, "_user_tz_name", return_value="Asia/Kolkata"), \
         patch("bootstrap_weekly_nudge.datetime") as dt:
        dt.now.return_value = fake_now
        dt.side_effect = lambda *a, **k: datetime(*a, **k)
        out = _parse(mod._next_sunday_9am_in_user_tz())
    assert out == datetime(2026, 6, 7, 3, 30)


# Silence the unused-import warning for timedelta — it's part of the
# datetime patch surface for clarity even if no test calls it directly.
_ = timedelta
