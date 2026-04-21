#!/usr/bin/env python3.13
"""
YouTube OAuth helper — one-shot setup for yt-analytics.readonly.

Fixes the 0/6 retention issue in content_engine.learning_loop by minting a
fresh refresh token with the right scopes. Uses google-auth-oauthlib's
InstalledAppFlow, which:

  1. Opens your default browser to Google's consent page
  2. You log in (your existing Chrome session handles 2FA transparently)
  3. You click "Allow"
  4. Google redirects to http://localhost:<random_port> with an auth code
  5. This script catches it, exchanges it for access+refresh tokens
  6. Writes everything back to .env

Pre-reqs (do these ONCE in Google Cloud Console before running this script):
  1. https://console.cloud.google.com/apis/credentials — open the project that
     issued your existing YouTube OAuth client
  2. APIs & Services → Library → enable "YouTube Analytics API"
  3. OAuth consent screen → Scopes → add:
        .../auth/youtube.upload
        .../auth/youtube.readonly
        .../auth/yt-analytics.readonly
     Republish the consent screen.
  4. APIs & Services → Credentials → open your OAuth 2.0 Client ID and
     ADD http://localhost to the Authorized redirect URIs (the script uses
     an ephemeral localhost port, so the redirect URI just needs to start
     with http://localhost — no port needed).
     Save.

Then run:
     python3.13 scripts/setup_youtube_oauth.py                    # Main channel (default)
     python3.13 scripts/setup_youtube_oauth.py --channel holyrave # Holy Rave channel

The --channel flag isolates credentials per channel:
  default → writes YOUTUBE_REFRESH_TOKEN (used by the existing Shorts
            pipeline on @robertjanmastenbroekofficial)
  holyrave → writes HOLYRAVE_REFRESH_TOKEN (used by the long-form
             Holy Rave channel publishing pipeline)

One OAuth app can authorize many channels. Run with --channel holyrave
while your Google account is switched to the Holy Rave channel to mint
a dedicated refresh token for it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

try:
    from google_auth_oauthlib.flow import InstalledAppFlow
except ImportError:
    print("✗ google-auth-oauthlib not installed. Run:")
    print("    python3.13 -m pip install google-auth-oauthlib")
    sys.exit(1)

PROJECT_ROOT = Path(__file__).parent.parent
ENV_FILE     = PROJECT_ROOT / ".env"

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


def _load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


def _write_env(updates: dict[str, str]) -> None:
    """Merge updates into .env, preserving ordering and comments."""
    text = ENV_FILE.read_text() if ENV_FILE.exists() else ""
    lines = text.splitlines()
    remaining = dict(updates)
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k in remaining:
                out.append(f"{k}={remaining.pop(k)}")
                continue
        out.append(line)
    for k, v in remaining.items():
        out.append(f"{k}={v}")
    ENV_FILE.write_text("\n".join(out).rstrip() + "\n")


def _prompt(label: str, existing: str | None = None) -> str:
    if existing:
        masked = existing[:6] + "…" + existing[-4:] if len(existing) > 12 else "(set)"
        reply = input(f"  {label} [{masked}] (Enter to keep): ").strip()
        return reply or existing
    return input(f"  {label}: ").strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Mint a YouTube OAuth refresh token.")
    parser.add_argument(
        "--channel",
        choices=["main", "holyrave"],
        default="main",
        help="Which channel this token authorizes. 'main' writes YOUTUBE_REFRESH_TOKEN "
             "(for the existing Shorts pipeline). 'holyrave' writes HOLYRAVE_REFRESH_TOKEN "
             "(for the Holy Rave long-form pipeline).",
    )
    args = parser.parse_args()
    refresh_env_key = "YOUTUBE_REFRESH_TOKEN" if args.channel == "main" else "HOLYRAVE_REFRESH_TOKEN"

    print("═══════════════════════════════════════════════════════════════")
    print(f"  YouTube OAuth setup — channel: {args.channel}")
    print(f"  will write: {refresh_env_key}")
    print("═══════════════════════════════════════════════════════════════")
    print()
    if args.channel == "holyrave":
        print("⚠  BEFORE clicking 'Allow' in the browser:")
        print("   make sure the Google account switcher at the top-right is")
        print("   set to the HOLY RAVE channel, not Robert-Jan Mastenbroek.")
        print("   (The channel active when you click Allow is what gets authorized.)")
        print()

    env = _load_env()
    client_id     = _prompt("Google OAuth Client ID",     env.get("YOUTUBE_CLIENT_ID"))
    client_secret = _prompt("Google OAuth Client Secret", env.get("YOUTUBE_CLIENT_SECRET"))

    if not client_id or not client_secret:
        print("✗ Client ID and Secret are required.")
        print("  Find them at: https://console.cloud.google.com/apis/credentials")
        return 1

    client_config = {
        "installed": {
            "client_id":     client_id,
            "client_secret": client_secret,
            "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
            "token_uri":     "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }

    print()
    print("Opening your browser for Google consent …")
    print("(if a browser doesn't open, copy the URL printed below)")
    print()

    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    # port=0 → OS picks an ephemeral port automatically
    creds = flow.run_local_server(port=0, open_browser=True, prompt="consent")

    if not creds.refresh_token:
        print("✗ Google did not return a refresh token.")
        print("  This usually means you've already granted this app access.")
        print("  Fix: go to https://myaccount.google.com/permissions , revoke")
        print("  access for your OAuth client, then re-run this script.")
        return 1

    updates = {
        "YOUTUBE_CLIENT_ID":     client_id,
        "YOUTUBE_CLIENT_SECRET": client_secret,
        refresh_env_key:         creds.refresh_token,
    }
    # Only update the access-token cache when writing the main-channel token
    # (the long-form pipeline mints its own access token per run from refresh).
    if args.channel == "main":
        updates["YOUTUBE_OAUTH_TOKEN"] = creds.token
    _write_env(updates)

    print()
    print("✓ Tokens written to .env")
    print(f"  access token:  {creds.token[:20]}…  (expires {creds.expiry})")
    print(f"  refresh token: {creds.refresh_token[:20]}…  (permanent)")
    print(f"  scopes:        {', '.join(creds.scopes)}")
    print()
    print("── Verification ──")

    # Smoke test against YouTube Analytics
    import requests
    from datetime import datetime, timedelta
    start = (datetime.utcnow() - timedelta(days=28)).date().isoformat()
    end   = datetime.utcnow().date().isoformat()
    resp = requests.get(
        "https://youtubeanalytics.googleapis.com/v2/reports",
        params={
            "ids":        "channel==MINE",
            "metrics":    "views,averageViewPercentage",
            "startDate":  start,
            "endDate":    end,
            "dimensions": "video",
        },
        headers={"Authorization": f"Bearer {creds.token}"},
        timeout=20,
    )
    if resp.status_code == 200:
        rows = resp.json().get("rows", []) or []
        print(f"  ✓ YT Analytics returned {len(rows)} video rows")
        if rows:
            print(f"    first row: video={rows[0][0]}, views={rows[0][1]}, "
                  f"avgPct={rows[0][2]:.1f}%")
    else:
        print(f"  ✗ YT Analytics returned {resp.status_code}")
        print(f"    {resp.text[:200]}")
        print("  Check that the YouTube Analytics API is enabled in the same")
        print("  project as the OAuth client, and that the consent screen")
        print("  has yt-analytics.readonly listed under Scopes.")
        return 1

    print()
    print("All set. Next run of content_engine/learning_loop.py will have")
    print("real completion_rate signal for every YT post.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
