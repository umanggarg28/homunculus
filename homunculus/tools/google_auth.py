"""Google OAuth token management — raw HTTP, no SDK, read-only scopes.

The agent's Google access is deliberately minimal:

- **Read-only scopes, enforced at the grant.** The refresh token is
  minted for `calendar.events.readonly` + `gmail.readonly` only, so even
  a fully compromised agent cannot send mail, mark anything read, or
  touch the calendar. Capability limits set where the model can't reach
  them — the same principle as the recurrence vocabulary and the
  docker-socket proxy.
- **Credentials live OUTSIDE the workspace** (`secrets/google/` at the
  repo root, mounted read-only into the containers). The agent's
  filesystem tools are workspace-scoped, so no tool call can read or
  exfiltrate the refresh token.

scripts/google_auth.py performs the one-time consent flow on the host
and writes token.json; this module only refreshes access tokens from it.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import httpx

from homunculus import REPO_ROOT

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/calendar.events.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
]

_TOKEN_URL = "https://oauth2.googleapis.com/token"


def _secrets_dir() -> Path:
    return Path(os.environ.get("HOMUNCULUS_GOOGLE_SECRETS_DIR", str(REPO_ROOT / "secrets" / "google")))


def client_secret_path() -> Path:
    return _secrets_dir() / "client_secret.json"


def token_path() -> Path:
    return _secrets_dir() / "token.json"


# Access tokens live ~1h; cache one per process and refresh a minute early.
_access_cache: tuple[float, str] | None = None


def load_client() -> tuple[str, str] | None:
    """(client_id, client_secret) from the downloaded OAuth client file.

    Accepts both shapes Google Cloud Console produces ("installed" for
    Desktop-app clients, "web" for web clients).
    """
    try:
        raw = json.loads(client_secret_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    blob = raw.get("installed") or raw.get("web") or {}
    cid, secret = blob.get("client_id"), blob.get("client_secret")
    if not cid or not secret:
        return None
    return cid, secret


def get_access_token() -> str | None:
    """A live access token, refreshed from the stored refresh token.

    Returns None when the account isn't connected (no client secret, no
    token file, or the refresh was rejected) — callers surface their
    *_UNAVAILABLE sentinel instead of failing the run.
    """
    global _access_cache
    now = time.time()
    if _access_cache is not None and _access_cache[0] > now:
        return _access_cache[1]

    client = load_client()
    if client is None:
        return None
    try:
        stored = json.loads(token_path().read_text(encoding="utf-8"))
        refresh_token = stored["refresh_token"]
    except (OSError, json.JSONDecodeError, KeyError):
        return None

    try:
        resp = httpx.post(_TOKEN_URL, data={
            "client_id": client[0],
            "client_secret": client[1],
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }, timeout=15)
        resp.raise_for_status()
        payload = resp.json()
        token = payload["access_token"]
        expires_in = float(payload.get("expires_in", 3600))
    except (httpx.HTTPError, KeyError, ValueError) as e:
        log.warning(f"[google_auth] token refresh failed: {e}")
        return None

    _access_cache = (now + expires_in - 60, token)
    return token


def api_get(url: str, params: dict) -> dict | None:
    """Authenticated GET against a Google API. None = not connected or
    the request failed (callers degrade to their sentinel)."""
    token = get_access_token()
    if token is None:
        return None
    try:
        resp = httpx.get(
            url, params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, ValueError) as e:
        log.warning(f"[google_auth] GET {url.split('?')[0]} failed: {e}")
        return None
