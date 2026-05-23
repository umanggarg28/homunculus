"""Notification tools: notify (push to user's Telegram)."""

from __future__ import annotations

import os

import httpx


def notify(text: str) -> str:
    """Push a proactive Telegram message. Interrupts the user — use sparingly."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_ALLOWED_USER_ID")
    if not token or not chat_id:
        return (
            "ERROR: Telegram is not configured (TELEGRAM_BOT_TOKEN or "
            "TELEGRAM_ALLOWED_USER_ID missing). Cannot send notification."
        )
    try:
        response = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10.0,
        )
    except httpx.HTTPError as e:
        return f"ERROR: Telegram request failed: {e}"
    if response.status_code != 200:
        return f"ERROR: Telegram API {response.status_code}: {response.text}"
    return f"Notification delivered ({len(text)} chars)."


