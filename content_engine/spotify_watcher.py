#!/usr/bin/env python3.13
"""
spotify_watcher.py — Daily Spotify follower fetcher.

Calls the public Spotify Web API (/v1/artists/{id}) via client-credentials
OAuth to pull the current follower count for RJM. Writes a row into
spotify_stats so the learning loop can compute daily follower deltas and
attribute them to the clip batch posted in the prior window.

Monthly listeners are NOT exposed by the public API — that stays manual
(spotify_tracker.py log <count>). Follower count is the closest automated
proxy for "did today's content grow the audience?".

Credentials:
  SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET in .env
  Get yours at https://developer.spotify.com/dashboard (free, ~2 min).

Usage:
  python3.13 spotify_watcher.py            # fetch + write today's row
  python3.13 spotify_watcher.py --dry-run  # fetch + print, no write
  python3.13 spotify_watcher.py --backfill # overwrite today if exists
"""

import argparse
import logging
import os
import sys
from datetime import date as _date
from pathlib import Path

import requests

PROJECT_DIR = Path(__file__).parent.parent

# ─── Env loader ──────────────────────────────────────────────────────────────

def _load_env():
    env_file = PROJECT_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

_load_env()

# Make outreach_agent/db importable
sys.path.insert(0, str(PROJECT_DIR / "outreach_agent"))
import db  # noqa: E402

# ─── Logging ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  spotify_watcher: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("spotify_watcher")

# ─── Config ──────────────────────────────────────────────────────────────────

SPOTIFY_ARTIST_ID = "2Seaafm5k1hAuCkpdq7yds"  # Robert-Jan Mastenbroek
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_BASE  = "https://api.spotify.com/v1"


# ─── Auth ────────────────────────────────────────────────────────────────────

def _get_access_token() -> str:
    """Client-credentials OAuth — no user login required."""
    client_id     = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise RuntimeError(
            "SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET not set in .env. "
            "Create a free app at https://developer.spotify.com/dashboard."
        )
    resp = requests.post(
        SPOTIFY_TOKEN_URL,
        data={"grant_type": "client_credentials"},
        auth=(client_id, client_secret),
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


# ─── Fetch ───────────────────────────────────────────────────────────────────

def fetch_artist_stats() -> dict:
    """Return {'followers': int, 'name': str, 'popularity': int}."""
    token = _get_access_token()
    resp = requests.get(
        f"{SPOTIFY_API_BASE}/artists/{SPOTIFY_ARTIST_ID}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return {
        "followers":  int(data.get("followers", {}).get("total", 0)),
        "name":       data.get("name", ""),
        "popularity": int(data.get("popularity", 0)),
    }


# ─── Persist ─────────────────────────────────────────────────────────────────

def _last_monthly_listeners() -> int:
    """Carry forward the most recent manually-logged monthly_listeners
    so the daily row has *something* in that column — the weekly manual
    update will overwrite it with the fresh number."""
    with db.get_conn() as conn:
        row = conn.execute(
            """SELECT monthly_listeners FROM spotify_stats
               WHERE monthly_listeners > 0
               ORDER BY date DESC, id DESC LIMIT 1"""
        ).fetchone()
    return int(row[0]) if row else 0


def write_row(followers: int, backfill: bool = False) -> int:
    """Insert or update today's auto_followers row in spotify_stats."""
    today = _date.today().isoformat()
    carry = _last_monthly_listeners()

    with db.get_conn() as conn:
        existing = conn.execute(
            "SELECT id, source FROM spotify_stats WHERE date = ? ORDER BY id DESC LIMIT 1",
            (today,),
        ).fetchone()

        # If today already has a manual row, do NOT overwrite — just insert
        # a second row tagged auto_followers so the manual one is preserved.
        if existing and existing[1] == "manual" and not backfill:
            cursor = conn.execute(
                """INSERT INTO spotify_stats (date, monthly_listeners, followers, source, notes)
                   VALUES (?,?,?,?,?)""",
                (today, carry, followers, "auto_followers",
                 "auto-polled after manual entry"),
            )
            return cursor.lastrowid

        # If today already has an auto_followers row, update it in place
        if existing and existing[1] == "auto_followers":
            conn.execute(
                "UPDATE spotify_stats SET followers=? WHERE id=?",
                (followers, existing[0]),
            )
            return existing[0]

        # Fresh row for today
        cursor = conn.execute(
            """INSERT INTO spotify_stats (date, monthly_listeners, followers, source, notes)
               VALUES (?,?,?,?,?)""",
            (today, carry, followers, "auto_followers", "auto-polled"),
        )
        return cursor.lastrowid


# ─── Main ────────────────────────────────────────────────────────────────────

def run(dry_run: bool = False, backfill: bool = False) -> dict:
    db.init_db()
    try:
        stats = fetch_artist_stats()
    except RuntimeError as e:
        log.warning(str(e))
        return {"success": False, "error": str(e)}
    except requests.HTTPError as e:
        log.error(f"Spotify API error: {e.response.status_code} {e.response.text[:200]}")
        return {"success": False, "error": str(e)}
    except Exception as e:
        log.error(f"Fetch failed: {e}")
        return {"success": False, "error": str(e)}

    followers = stats["followers"]
    log.info(f"{stats['name']} — followers={followers}  popularity={stats['popularity']}")

    if dry_run:
        log.info("[dry-run] not writing")
        return {"success": True, "followers": followers, "dry_run": True}

    row_id = write_row(followers, backfill=backfill)
    log.info(f"Wrote spotify_stats row id={row_id}")
    return {"success": True, "followers": followers, "row_id": row_id}


def main():
    parser = argparse.ArgumentParser(description="Daily Spotify follower watcher.")
    parser.add_argument("--dry-run",  action="store_true", help="Don't write to db.")
    parser.add_argument("--backfill", action="store_true",
                        help="Overwrite today's row even if manual entry exists.")
    args = parser.parse_args()
    result = run(dry_run=args.dry_run, backfill=args.backfill)
    if not result.get("success"):
        sys.exit(1)


if __name__ == "__main__":
    main()
