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


#: Why the most recent refresh failed, for `doctor` and for the operator.
#: A dead grant and a flaky network both surface to the model as the same
#: GMAIL_UNAVAILABLE sentinel -- correctly, since neither is the model's
#: business -- but they need opposite responses from the human: one is
#: "wait", the other is "re-consent, nothing will heal on its own".
_LAST_FAILURE: dict[str, str] = {}

REVOKED_REMEDY = (
    "the stored Google refresh token has been revoked or expired, so no "
    "retry will recover it. Re-consent on the host with: "
    "uv run python scripts/google_auth.py"
)


def _note_grant_state(resp: httpx.Response) -> None:
    """Classify a 400 from the token endpoint.

    Google answers a dead grant with `invalid_grant`, which is permanent
    until a human re-consents -- the same shape as a 404 from a withdrawn
    model slug, and it deserves the same treatment: say so once, loudly,
    instead of retrying forever and reporting "not connected" every day.
    """
    try:
        body = resp.json()
    except ValueError:
        body = {}
    error = str(body.get("error") or "")
    if error == "invalid_grant":
        _LAST_FAILURE["reason"] = "revoked"
        log.error(f"[google_auth] {REVOKED_REMEDY}")
    else:
        _LAST_FAILURE["reason"] = error or "bad_request"
        log.warning(f"[google_auth] token refresh rejected: {error or resp.status_code}")


def unavailable_suffix() -> str:
    """Text appended to a Google tool's sentinel when the cause is known.

    The sentinel says the source is unreachable, which is all the MODEL needs
    -- it omits the section either way. But the message is relayed to a PERSON
    when the whole run is an outage notice, and "Google account not connected"
    told them nothing they could act on: it reads as a transient glitch when in
    fact nothing will change until they re-consent. Naming the remedy turns a
    daily nuisance into a one-time chore.
    """
    if _LAST_FAILURE.get("reason") != "revoked":
        return ""
    return f" Cause: {REVOKED_REMEDY}"


def grant_failure_reason() -> str | None:
    """'revoked', 'transient', or a provider error code; None if healthy."""
    return _LAST_FAILURE.get("reason")


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
        if resp.status_code == 400:
            _note_grant_state(resp)
            return None
        resp.raise_for_status()
        payload = resp.json()
        token = payload["access_token"]
        expires_in = float(payload.get("expires_in", 3600))
    except (httpx.HTTPError, KeyError, ValueError) as e:
        log.warning(f"[google_auth] token refresh failed: {e}")
        _LAST_FAILURE["reason"] = "transient"
        return None
    _LAST_FAILURE.pop("reason", None)

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
