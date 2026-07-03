"""One-time Google OAuth consent — run on the HOST, not in a container.

    uv run python scripts/google_auth.py

Prerequisites (≈3 minutes, once):
  1. console.cloud.google.com → create (or pick) a project.
  2. Enable the "Google Calendar API" and "Gmail API".
  3. OAuth consent screen → External → add yourself as a test user.
  4. Credentials → Create credentials → OAuth client ID → **Desktop app**.
  5. Download the JSON and save it as  secrets/google/client_secret.json

The script opens a browser for consent, catches the redirect on a local
loopback port, exchanges the code (with PKCE), and writes
secrets/google/token.json (mode 0600). Scopes are READ-ONLY calendar +
gmail — the agent physically cannot send mail or modify anything.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import secrets as pysecrets
import socket
import sys
import threading
import urllib.parse
import webbrowser

import httpx

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from homunculus.tools.google_auth import (  # noqa: E402
    SCOPES,
    client_secret_path,
    load_client,
    token_path,
)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"


def main() -> int:
    client = load_client()
    if client is None:
        print(f"ERROR: no OAuth client at {client_secret_path()}")
        print(__doc__)
        return 1
    client_id, client_secret = client

    # Loopback receiver on a free port.
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    redirect_uri = f"http://127.0.0.1:{port}"

    verifier = base64.urlsafe_b64encode(pysecrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    state = pysecrets.token_urlsafe(16)

    auth_url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",   # → refresh token
        "prompt": "consent",        # force refresh token even on re-grant
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    })

    code_holder: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if params.get("state", [""])[0] != state:
                self.send_response(400)
                self.end_headers()
                return
            code_holder["code"] = params.get("code", [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h2>Homunculus connected. You can close this tab.</h2>")

        def log_message(self, *args):  # silence request logging
            pass

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    print("Opening browser for Google consent (read-only calendar + gmail)…")
    print(f"If it doesn't open, visit:\n\n{auth_url}\n")
    webbrowser.open(auth_url)
    thread.join(timeout=300)
    server.server_close()

    code = code_holder.get("code")
    if not code:
        print("ERROR: no authorization code received (timed out or denied).")
        return 1

    resp = httpx.post(TOKEN_URL, data={
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "code_verifier": verifier,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    refresh = payload.get("refresh_token")
    if not refresh:
        print("ERROR: Google returned no refresh_token. Remove the app's prior "
              "grant at myaccount.google.com/permissions and rerun.")
        return 1

    out = token_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "refresh_token": refresh,
        "scopes": SCOPES,
    }, indent=2), encoding="utf-8")
    out.chmod(0o600)
    print(f"✓ Wrote {out} (0600). Restart the stack (docker compose up -d) "
          "and the calendar/gmail tools go live.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
