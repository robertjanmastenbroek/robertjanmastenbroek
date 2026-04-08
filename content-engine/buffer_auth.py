"""
Buffer API key setup — takes ~60 seconds.

Buffer now uses a simple API key (no OAuth required).

Steps:
  1. Go to https://publish.buffer.com/settings/api
  2. Generate an API key
  3. Run: python3 buffer_auth.py
  4. Paste your key when prompted — saved to .env automatically.
"""

import os
import sys
from pathlib import Path

import requests

CC_ROOT  = Path("~/Documents/Robert-Jan Mastenbroek Command Centre").expanduser()
ENV_PATH = CC_ROOT / ".env"


def _save_key(key: str):
    lines = []
    found = False
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            if line.startswith("BUFFER_API_KEY=") or line.startswith("BUFFER_ACCESS_TOKEN="):
                lines.append(f"BUFFER_API_KEY={key}")
                found = True
            else:
                lines.append(line)
    if not found:
        lines.append(f"BUFFER_API_KEY={key}")
    ENV_PATH.write_text("\n".join(lines) + "\n")
    print(f"\n✅ Key saved to {ENV_PATH}")


def main():
    print("━━━ Buffer API Key Setup ━━━━━━━━━━━━━━━━━━━━━━━━━")
    print()
    print("1. Open: https://publish.buffer.com/settings/api")
    print("2. Click 'Create a Token' (or copy your existing one)")
    print("3. Paste it below.")
    print()

    key = input("API Key: ").strip()
    if not key:
        print("ERROR: No key entered.")
        sys.exit(1)

    print("\nVerifying key...")
    resp = requests.post(
        "https://api.buffer.com",
        json={"query": "query { account { id email } }"},
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        timeout=15,
    )

    if resp.status_code != 200:
        print(f"ERROR: API returned {resp.status_code}: {resp.text[:200]}")
        sys.exit(1)

    data = resp.json()
    if "errors" in data or not data.get("data", {}).get("account", {}).get("id"):
        print(f"ERROR: Key invalid — {data.get('errors', resp.text)[:200]}")
        sys.exit(1)

    email = data["data"]["account"].get("email", "")
    print(f"✅ Key valid — account: {email}")

    _save_key(key)
    print()
    print("All set. Run your daily posts now:")
    print("  python3 daily_run.py")


if __name__ == "__main__":
    main()
