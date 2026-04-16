#!/usr/bin/env python3.13
"""
Instagram / Meta Graph API OAuth helper — one-shot setup.

Fixes the "INSTAGRAM_USER_ID is not numeric" warning in learning_loop by:

  1. Running the Facebook OAuth flow for the permissions Graph API insights
     need (instagram_basic + instagram_manage_insights + pages_show_list +
     pages_read_engagement + business_management).
  2. Exchanging the short-lived token for a 60-day long-lived token.
  3. Walking /me/accounts → instagram_business_account to resolve the
     numeric Business Account ID for the connected IG handle.
  4. Writing INSTAGRAM_ACCESS_TOKEN + INSTAGRAM_USER_ID back to .env.
  5. Smoke-testing by fetching the last 3 IG media items via /insights.

Pre-reqs (do these ONCE in developers.facebook.com before running this script):

  1. https://developers.facebook.com/apps/ — open your Holy Rave app (or
     create a new Business-type app if you don't have one).

  2. Settings → Basic → copy:
        App ID
        App Secret
     If the App Secret is hidden, click "Show" (may require password).

  3. Add product "Facebook Login for Business" if not already added:
        Dashboard → "+ Add Product" → "Facebook Login for Business"

  4. Facebook Login for Business → Settings → Valid OAuth Redirect URIs →
     add:
        http://localhost:8765/
     (the trailing slash is important for Meta). Click Save.

  5. App Review → Permissions & Features → request or confirm access to:
        instagram_basic
        instagram_manage_insights
        pages_show_list
        pages_read_engagement
        business_management
     In development mode, only users added as "testers" or "admins" of
     the app can grant these — make sure your personal FB account is
     listed under Roles → Roles.

  6. Make sure the IG account you want insights for
     (@robertjanmastenbroek or @holyraveofficial) is:
        - a Professional (Business or Creator) account
        - connected to a Facebook Page that YOUR personal FB account manages

Then run:
     python3.13 scripts/setup_instagram_oauth.py

The script will ask for your App ID and App Secret once, then open your
browser to the Meta consent screen. Click "Continue as [you]" → "Continue"
on the permission review → done. Tokens + numeric user ID land in .env
automatically.
"""
from __future__ import annotations

import http.server
import json
import os
import socketserver
import sys
import threading
import urllib.parse
import webbrowser
from pathlib import Path

try:
    import requests
except ImportError:
    print("✗ requests not installed. Run:")
    print("    python3.13 -m pip install requests")
    sys.exit(1)

PROJECT_ROOT = Path(__file__).parent.parent
ENV_FILE     = PROJECT_ROOT / ".env"

GRAPH_API_VERSION = "v21.0"
GRAPH_BASE        = f"https://graph.facebook.com/{GRAPH_API_VERSION}"
AUTH_BASE         = f"https://www.facebook.com/{GRAPH_API_VERSION}/dialog/oauth"

REDIRECT_HOST = "localhost"
REDIRECT_PORT = 8765
REDIRECT_URI  = f"http://{REDIRECT_HOST}:{REDIRECT_PORT}/"

SCOPES = [
    "instagram_basic",
    "instagram_manage_insights",
    "pages_show_list",
    "pages_read_engagement",
    "business_management",
]

# ─── .env helpers ────────────────────────────────────────────────────────────

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


def _prompt(label: str, existing: str | None = None, secret: bool = False) -> str:
    if existing:
        masked = existing[:6] + "…" + existing[-4:] if len(existing) > 12 else "(set)"
        reply = input(f"  {label} [{masked}] (Enter to keep): ").strip()
        return reply or existing
    if secret:
        import getpass
        return getpass.getpass(f"  {label}: ").strip()
    return input(f"  {label}: ").strip()


# ─── Tiny local redirect server ──────────────────────────────────────────────

class _OAuthHandler(http.server.BaseHTTPRequestHandler):
    received: dict = {}

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        _OAuthHandler.received.update({k: v[0] for k, v in params.items()})

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        if "code" in params:
            html = (
                "<html><body style='font-family:sans-serif;padding:40px;"
                "background:#111;color:#eee;'>"
                "<h2>✓ Facebook authorization received</h2>"
                "<p>You can close this tab and return to the terminal.</p>"
                "</body></html>"
            )
        else:
            err  = params.get("error_description", params.get("error", ["unknown"]))[0]
            html = (
                "<html><body style='font-family:sans-serif;padding:40px;"
                "background:#111;color:#eee;'>"
                "<h2>✗ Facebook authorization failed</h2>"
                f"<p style='color:#f88;'>{err}</p>"
                "</body></html>"
            )
        self.wfile.write(html.encode())

    def log_message(self, *args, **kwargs):  # silence default access log
        return


def _run_local_server(timeout: int = 180) -> dict:
    """Run a one-shot HTTP server on REDIRECT_PORT, wait for one GET, return params."""
    _OAuthHandler.received = {}
    httpd = socketserver.TCPServer((REDIRECT_HOST, REDIRECT_PORT), _OAuthHandler)
    httpd.timeout = 1
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    import time
    start = time.time()
    try:
        while time.time() - start < timeout:
            if _OAuthHandler.received:
                time.sleep(0.5)   # allow the response page to flush
                return dict(_OAuthHandler.received)
            time.sleep(0.2)
    finally:
        httpd.shutdown()
    return {}


# ─── Main flow ───────────────────────────────────────────────────────────────

def main() -> int:
    print("═══════════════════════════════════════════════════════════════")
    print("  Instagram / Meta Graph API — OAuth setup")
    print("═══════════════════════════════════════════════════════════════")
    print()

    env = _load_env()
    app_id     = _prompt("Meta App ID",     env.get("META_APP_ID"))
    app_secret = _prompt("Meta App Secret", env.get("META_APP_SECRET"), secret=True)

    if not app_id or not app_secret:
        print("✗ App ID and App Secret are required.")
        print("  Find them at: https://developers.facebook.com/apps/ → your")
        print("  app → Settings → Basic")
        return 1

    # ── Step 1: build auth URL
    auth_params = {
        "client_id":     app_id,
        "redirect_uri":  REDIRECT_URI,
        "scope":         ",".join(SCOPES),
        "response_type": "code",
        "state":         "rjm_setup",
    }
    auth_url = f"{AUTH_BASE}?{urllib.parse.urlencode(auth_params)}"

    print()
    print("Opening your browser to the Facebook consent screen …")
    print("(if it doesn't open, copy this URL manually:)")
    print(f"  {auth_url}")
    print()
    webbrowser.open(auth_url)

    # ── Step 2: wait for the redirect on localhost:8765
    print(f"Waiting for redirect to {REDIRECT_URI} (up to 3 minutes) …")
    received = _run_local_server(timeout=180)

    if "code" not in received:
        err = received.get("error_description") or received.get("error") or \
              "timed out waiting for Facebook redirect"
        print(f"✗ OAuth failed: {err}")
        if "redirect_uri" in (received.get("error_message") or ""):
            print(f"  Make sure {REDIRECT_URI} is listed in your app's")
            print("  'Valid OAuth Redirect URIs' under Facebook Login for Business → Settings.")
        return 1

    code = received["code"]
    print("✓ Authorization code received")

    # ── Step 3: exchange code for short-lived access token
    print("Exchanging code for short-lived access token …")
    resp = requests.get(
        f"{GRAPH_BASE}/oauth/access_token",
        params={
            "client_id":     app_id,
            "client_secret": app_secret,
            "redirect_uri":  REDIRECT_URI,
            "code":          code,
        },
        timeout=20,
    )
    if resp.status_code != 200:
        print(f"✗ Token exchange failed: {resp.status_code} {resp.text[:300]}")
        return 1
    short_token = resp.json().get("access_token", "")
    if not short_token:
        print(f"✗ No access_token in response: {resp.text[:300]}")
        return 1
    print(f"✓ Short-lived token acquired ({short_token[:20]}…)")

    # ── Step 4: exchange short-lived for long-lived 60-day token
    print("Exchanging short-lived → long-lived (60-day) token …")
    resp = requests.get(
        f"{GRAPH_BASE}/oauth/access_token",
        params={
            "grant_type":        "fb_exchange_token",
            "client_id":         app_id,
            "client_secret":     app_secret,
            "fb_exchange_token": short_token,
        },
        timeout=20,
    )
    if resp.status_code != 200:
        print(f"✗ Long-lived exchange failed: {resp.status_code} {resp.text[:300]}")
        return 1
    long_token = resp.json().get("access_token", "")
    print(f"✓ Long-lived token acquired ({long_token[:20]}…)")

    # ── Step 5: list pages and resolve IG Business Account ID
    print("Listing your Facebook Pages …")
    resp = requests.get(
        f"{GRAPH_BASE}/me/accounts",
        params={"access_token": long_token, "limit": 100},
        timeout=20,
    )
    if resp.status_code != 200:
        print(f"✗ /me/accounts failed: {resp.status_code} {resp.text[:300]}")
        return 1
    pages = resp.json().get("data", []) or []
    if not pages:
        print("✗ No Facebook Pages found on your account.")
        print("  The IG account you want insights for MUST be connected to a")
        print("  Facebook Page that this user manages. Create one at:")
        print("  https://www.facebook.com/pages/create")
        return 1

    # For each page, check for instagram_business_account
    ig_options: list[tuple[str, str, str]] = []  # (page_name, page_id, ig_id)
    for page in pages:
        pid   = page.get("id", "")
        pname = page.get("name", "(unnamed)")
        r = requests.get(
            f"{GRAPH_BASE}/{pid}",
            params={
                "fields":       "instagram_business_account,connected_instagram_account",
                "access_token": long_token,
            },
            timeout=15,
        )
        if r.status_code != 200:
            continue
        body = r.json()
        ig_id = (body.get("instagram_business_account") or {}).get("id") or \
                (body.get("connected_instagram_account") or {}).get("id")
        if ig_id:
            # Fetch the IG handle for nicer output
            ig_handle = ""
            r2 = requests.get(
                f"{GRAPH_BASE}/{ig_id}",
                params={"fields": "username", "access_token": long_token},
                timeout=15,
            )
            if r2.status_code == 200:
                ig_handle = r2.json().get("username", "")
            label = f"@{ig_handle}" if ig_handle else f"IG id {ig_id}"
            ig_options.append((f"{pname} → {label}", pid, ig_id))

    if not ig_options:
        print("✗ None of your Pages have a connected Instagram Business account.")
        print("  Fix: in the Instagram app, go to Settings → Account → Switch")
        print("  to Professional Account, then Settings → Business → Connect a")
        print("  Facebook Page.")
        return 1

    print()
    print("Found these IG Business accounts:")
    for i, (label, _pid, ig_id) in enumerate(ig_options):
        print(f"  [{i}] {label}  (id={ig_id})")

    if len(ig_options) == 1:
        chosen_idx = 0
        print(f"Auto-selecting the only option: [0]")
    else:
        while True:
            pick = input(f"Pick one [0-{len(ig_options)-1}]: ").strip()
            try:
                chosen_idx = int(pick)
                if 0 <= chosen_idx < len(ig_options):
                    break
            except Exception:
                pass
            print("  Invalid, try again.")

    chosen_label, _pid, chosen_ig_id = ig_options[chosen_idx]
    print(f"✓ Selected {chosen_label}  (IG id {chosen_ig_id})")

    # ── Step 6: write .env
    updates = {
        "META_APP_ID":            app_id,
        "META_APP_SECRET":        app_secret,
        "INSTAGRAM_ACCESS_TOKEN": long_token,
        "INSTAGRAM_USER_ID":      chosen_ig_id,
    }
    _write_env(updates)
    print()
    print("✓ Credentials written to .env")

    # ── Step 7: verification — fetch last 3 media + insights
    print()
    print("── Verification ──")
    resp = requests.get(
        f"{GRAPH_BASE}/{chosen_ig_id}/media",
        params={
            "fields":       "id,caption,media_type,timestamp,permalink",
            "limit":        3,
            "access_token": long_token,
        },
        timeout=20,
    )
    if resp.status_code != 200:
        print(f"  ✗ /media failed: {resp.status_code} {resp.text[:200]}")
        return 1
    media = resp.json().get("data", []) or []
    print(f"  ✓ Fetched {len(media)} recent IG media items")
    for m in media:
        cap = (m.get("caption") or "").replace("\n", " ")[:60]
        print(f"    · {m.get('timestamp','?')}  {cap}")

    # Try insights on the first one
    if media:
        mid = media[0]["id"]
        r = requests.get(
            f"{GRAPH_BASE}/{mid}/insights",
            params={
                "metric":       "plays,reach,saved,shares,total_interactions,comments,likes",
                "access_token": long_token,
            },
            timeout=15,
        )
        if r.status_code == 200:
            vals = {}
            for d in r.json().get("data", []):
                v = d.get("values", [{}])
                if v:
                    vals[d.get("name", "")] = v[0].get("value", 0)
            print(f"  ✓ /insights returned: {vals}")
        else:
            print(f"  ⚠ /insights returned {r.status_code}: {r.text[:200]}")
            print("    (common: Reels use video_views, feed posts use plays — safe to ignore)")

    print()
    print("All set. Next run of content_engine/learning_loop.py will pick up")
    print("real IG reach/saves/shares for every Reel.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
