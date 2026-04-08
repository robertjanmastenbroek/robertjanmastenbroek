"""
Buffer OAuth helper — get your API access token in ~60 seconds.

Steps:
  1. Go to https://buffer.com/developers/apps/create
  2. Create an app (name: "Holy Rave", callback: http://localhost:8080)
  3. Copy your Client ID and Client Secret
  4. Run: python3 buffer_auth.py
  5. It opens Buffer in your browser, you click Authorize, done.
  6. Token is saved to your .env automatically.
"""

import os
import sys
import webbrowser
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

import requests

CC_ROOT  = Path("~/Documents/Robert-Jan Mastenbroek Command Centre").expanduser()
ENV_PATH = CC_ROOT / ".env"
PORT     = 8080
REDIRECT = f"http://localhost:{PORT}"


class _Handler(BaseHTTPRequestHandler):
    code = None

    def do_GET(self):
        params = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
        _Handler.code = params.get("code")
        self.send_response(200)
        self.end_headers()
        msg = b"<h2>Authorized. You can close this tab.</h2>"
        self.wfile.write(msg)

    def log_message(self, *args):
        pass  # silence request logs


def _save_token(token: str):
    lines = []
    found = False
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            if line.startswith("BUFFER_ACCESS_TOKEN="):
                lines.append(f"BUFFER_ACCESS_TOKEN={token}")
                found = True
            else:
                lines.append(line)
    if not found:
        lines.append(f"BUFFER_ACCESS_TOKEN={token}")
    ENV_PATH.write_text("\n".join(lines) + "\n")
    print(f"\n✅ Token saved to {ENV_PATH}")


def main():
    print("━━━ Buffer OAuth Setup ━━━━━━━━━━━━━━━━━━━━━━━━━")
    print()
    print("1. Open: https://buffer.com/developers/apps/create")
    print("2. App name: Holy Rave")
    print(f"3. Callback URL: {REDIRECT}")
    print("4. Submit, then paste your Client ID and Client Secret below.")
    print()

    client_id     = input("Client ID:     ").strip()
    client_secret = input("Client Secret: ").strip()

    if not client_id or not client_secret:
        print("ERROR: Both fields required.")
        sys.exit(1)

    auth_url = (
        "https://bufferapp.com/oauth2/authorize"
        f"?client_id={client_id}"
        f"&redirect_uri={urllib.parse.quote(REDIRECT)}"
        "&response_type=code"
    )

    print(f"\nOpening Buffer in your browser...")
    webbrowser.open(auth_url)
    print("(If it doesn't open, go to this URL manually:)")
    print(auth_url)
    print()
    print("Waiting for you to authorize...")

    server = HTTPServer(("localhost", PORT), _Handler)
    server.handle_request()

    code = _Handler.code
    if not code:
        print("ERROR: No authorization code received.")
        sys.exit(1)

    print("Got authorization code — exchanging for access token...")
    resp = requests.post(
        "https://api.bufferapp.com/1/oauth2/token.json",
        data={
            "client_id":     client_id,
            "client_secret": client_secret,
            "redirect_uri":  REDIRECT,
            "code":          code,
            "grant_type":    "authorization_code",
        }
    )

    if resp.status_code != 200:
        print(f"ERROR: Token exchange failed ({resp.status_code}): {resp.text}")
        sys.exit(1)

    token = resp.json().get("access_token")
    if not token:
        print(f"ERROR: No access_token in response: {resp.text}")
        sys.exit(1)

    _save_token(token)
    print(f"Token: {token[:12]}...{token[-6:]}")
    print()
    print("All set. Run your daily posts now:")
    print("  python3 daily_run.py")


if __name__ == "__main__":
    main()
