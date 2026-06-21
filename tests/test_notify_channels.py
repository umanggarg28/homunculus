"""Multi-channel notify: web-feed-always + additive push channels.

Telegram is blocked in India (2026-06), so delivery must (a) always record to
the web app feed so nothing is lost, and (b) fan out to whatever channels are
configured (Telegram + Discord), additively. Loaded standalone so it doesn't
clobber the tools.notify stub other tests install.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest

_spec = importlib.util.spec_from_file_location(
    "notify_channels_under_test", Path(__file__).parent.parent / "homunculus" / "tools" / "notify.py"
)
notify = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(notify)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMUNCULUS_MEMORY_DIR", str(tmp_path))
    for v in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_USER_ID", "DISCORD_BOT_TOKEN", "DISCORD_CHANNEL_ID"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(notify.time, "sleep", lambda *_: None)
    notify._send_history.clear()  # module-global rate-limit state; isolate per test


class _Resp:
    def __init__(self, status): self.status_code = status; self.text = ""


def test_records_to_feed_with_no_channels():
    """The keystone web-fallback: even with NO push channel configured (the
    Telegram-block case), notify still succeeds by recording to the feed."""
    out = notify.notify("brief for today")
    assert "web app feed" in out
    assert "DELIVERED" in out
    assert not out.startswith("ERROR")
    feed = (Path(notify.os.environ["HOMUNCULUS_MEMORY_DIR"]) / "_notifications.jsonl").read_text()
    assert "brief for today" in feed


def test_records_once_not_per_channel(monkeypatch):
    """Recorded exactly once even when channels also fire (no doubled feed rows)."""
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "t")
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "123")
    with patch.object(notify, "_post_with_retry", return_value=(_Resp(200), None)):
        notify.notify("hello")
    feed = (Path(notify.os.environ["HOMUNCULUS_MEMORY_DIR"]) / "_notifications.jsonl").read_text().strip()
    assert feed.count("hello") == 1


def test_discord_channel_included_when_configured(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "999")
    assert [n for n, _ in notify._channel_senders()] == ["discord"]


def test_both_channels_fan_out(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tg")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "1")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "dc")
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "2")
    with patch.object(notify, "_post_with_retry", return_value=(_Resp(200), None)):
        r = notify.deliver("ping")
    assert set(r["delivered"]) == {"telegram", "discord"}
    assert r["failed"] == []


def test_one_channel_down_still_delivers_via_other(monkeypatch):
    """Telegram blocked but Discord up → still delivered; task must not fail."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tg")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "1")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "dc")
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "2")

    def fake_post(url, json_payload, headers=None):
        if "telegram" in url:
            return None, "ConnectTimeout"  # blocked
        return _Resp(200), None

    with patch.object(notify, "_post_with_retry", side_effect=fake_post):
        out = notify.notify("brief")
    assert "discord" in out
    assert "telegram" in out  # the skipped channel is named, calmly
    assert not out.startswith("ERROR")  # delivered via discord + feed → success
    # Weak-model-safe contract: a partial success must NOT read as a failure, or
    # a model (or a "retry on error" skill) resends and the user gets duplicates.
    assert "do not resend" in out.lower()
    for failure_word in ("ERROR", "failed", "timed out", "timeout"):
        assert failure_word.lower() not in out.lower(), failure_word


def test_discord_truncates_at_2000(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "999")
    captured = {}

    def fake_post(url, json_payload, headers=None):
        captured["len"] = len(json_payload["content"])
        return _Resp(200), None

    with patch.object(notify, "_post_with_retry", side_effect=fake_post):
        notify._send_to_discord("X" * 5000)
    assert captured["len"] == 2000


def test_duplicate_within_window_is_suppressed(monkeypatch):
    """The keystone idempotency guarantee: the same text sent twice in quick
    succession (a retry storm) reaches the user once. The second call neither
    records to the feed again nor re-fires any push channel."""
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "t")
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "123")
    calls = {"n": 0}

    def fake_post(url, json_payload, headers=None):
        calls["n"] += 1
        return _Resp(200), None

    with patch.object(notify, "_post_with_retry", side_effect=fake_post):
        first = notify.notify("🧠 Quiz — same question")
        second = notify.notify("🧠 Quiz — same question")

    assert "DELIVERED" in first and not first.startswith("ERROR")
    assert "ALREADY DELIVERED" in second
    assert "do not resend" in second.lower()
    assert calls["n"] == 1  # discord pushed once, not twice
    feed = (Path(notify.os.environ["HOMUNCULUS_MEMORY_DIR"]) / "_notifications.jsonl").read_text()
    assert feed.count("same question") == 1


def test_different_text_is_not_suppressed(monkeypatch):
    """Dedup keys on exact text — distinct messages always go through."""
    with patch.object(notify, "_post_with_retry", return_value=(_Resp(200), None)):
        a = notify.notify("first message")
        b = notify.notify("second message")
    assert "DELIVERED" in a and "DELIVERED" in b
    assert "ALREADY DELIVERED" not in b


def test_dedup_disabled_when_window_zero(monkeypatch):
    """Operators can turn the guard off; then identical resends go through."""
    monkeypatch.setattr(notify, "NOTIFY_DEDUP_WINDOW_S", 0)
    with patch.object(notify, "_post_with_retry", return_value=(_Resp(200), None)):
        notify.notify("repeat me")
        second = notify.notify("repeat me")
    assert "ALREADY DELIVERED" not in second


def test_discord_link_flattened():
    assert notify._markdown_to_discord("[Candy](https://lc.com/candy)") == "Candy: https://lc.com/candy"
