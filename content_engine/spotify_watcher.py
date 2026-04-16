"""
Spotify Watcher
Tracks RJM's Spotify followers + monthly listeners, detects new releases,
and exposes track popularity + audio features for the content pipeline.

Reads/writes a small JSON cache at data/spotify_watcher.json. All API calls
are best-effort: if Spotify credentials are missing, functions return safe
defaults so the pipeline never crashes.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from datetime import date as _date
from pathlib import Path
from typing import Optional

import requests

PROJECT_DIR = Path(__file__).parent.parent
DATA_DIR = PROJECT_DIR / "data"
CACHE_FILE = DATA_DIR / "spotify_watcher.json"

logger = logging.getLogger(__name__)
# The plan snippets below use `log` — alias to keep them in sync.
log = logger


# ─── Follower / Listener Tracking ────────────────────────────────────────────

def _load_cache() -> dict:
    if not CACHE_FILE.exists():
        return {"history": []}
    try:
        return json.loads(CACHE_FILE.read_text())
    except Exception:
        return {"history": []}


def _save_cache(cache: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=2))


def record_followers(followers: int, monthly_listeners: int = 0) -> dict:
    """Append today's follower / listener reading to the cache."""
    cache = _load_cache()
    entry = {
        "date": str(_date.today()),
        "followers": followers,
        "monthly_listeners": monthly_listeners,
    }
    cache.setdefault("history", []).append(entry)
    _save_cache(cache)
    return entry


def latest_followers() -> Optional[dict]:
    """Return the most recent follower reading or None."""
    cache = _load_cache()
    history = cache.get("history") or []
    return history[-1] if history else None


# ─── New Release Detection ───────────────────────────────────────────────────

ARTIST_ID = "2Seaafm5k1hAuCkpdq7yds"


def _get_client_token() -> Optional[str]:
    """Get Spotify client credentials token."""
    client_id = os.environ.get("SPOTIFY_CLIENT_ID", "")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        log.warning("SPOTIFY_CLIENT_ID or SPOTIFY_CLIENT_SECRET not set")
        return None

    auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    try:
        resp = requests.post(
            "https://accounts.spotify.com/api/token",
            headers={"Authorization": f"Basic {auth}"},
            data={"grant_type": "client_credentials"},
            timeout=15,
        )
    except Exception as e:
        log.error(f"Client token request failed: {e}")
        return None
    if resp.status_code == 200:
        return resp.json()["access_token"]
    log.error(f"Client token failed: {resp.status_code} {resp.text[:200]}")
    return None


def _get_user_token() -> Optional[str]:
    """Get Spotify user token via refresh token (Premium required)."""
    refresh_token = os.environ.get("SPOTIFY_USER_REFRESH_TOKEN", "")
    client_id = os.environ.get("SPOTIFY_CLIENT_ID", "")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
    if not all([refresh_token, client_id, client_secret]):
        return None

    auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    try:
        resp = requests.post(
            "https://accounts.spotify.com/api/token",
            headers={"Authorization": f"Basic {auth}"},
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            timeout=15,
        )
    except Exception as e:
        log.error(f"User token request failed: {e}")
        return None
    if resp.status_code == 200:
        data = resp.json()
        # Persist new refresh token if provided
        new_refresh = data.get("refresh_token")
        if new_refresh and new_refresh != refresh_token:
            _update_env("SPOTIFY_USER_REFRESH_TOKEN", new_refresh)
        return data["access_token"]
    return None


def fetch_new_releases() -> list[dict]:
    """Fetch artist's recent singles. Returns [{id, title, release_date, tracks}]."""
    token = _get_client_token()
    if not token:
        return []

    try:
        resp = requests.get(
            f"https://api.spotify.com/v1/artists/{ARTIST_ID}/albums",
            headers={"Authorization": f"Bearer {token}"},
            params={"include_groups": "single", "limit": 10, "market": "US"},
            timeout=15,
        )
        if resp.status_code != 200:
            return []

        albums = resp.json().get("items", [])
        releases = []
        for album in albums:
            releases.append({
                "id": album["id"],
                "title": album["name"],
                "release_date": album.get("release_date", ""),
                "tracks": [],
            })
            # Fetch tracks for each single
            tracks_resp = requests.get(
                f"https://api.spotify.com/v1/albums/{album['id']}/tracks",
                headers={"Authorization": f"Bearer {token}"},
                params={"limit": 5},
                timeout=15,
            )
            if tracks_resp.status_code == 200:
                for track in tracks_resp.json().get("items", []):
                    releases[-1]["tracks"].append({
                        "id": track["id"],
                        "title": track["name"],
                    })
        return releases
    except Exception as e:
        log.error(f"fetch_new_releases failed: {e}")
        return []


def fetch_track_popularity(track_id: str) -> int:
    """Fetch a track's popularity score (0-100)."""
    token = _get_client_token()
    if not token:
        return 0
    try:
        resp = requests.get(
            f"https://api.spotify.com/v1/tracks/{track_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        if resp.status_code == 200:
            return resp.json().get("popularity", 0)
        return 0
    except Exception:
        return 0


def fetch_audio_features(track_id: str) -> dict:
    """Fetch track audio features (BPM, energy, danceability, valence)."""
    token = _get_client_token()
    if not token:
        return {"bpm": 128, "energy": 0.7, "danceability": 0.7, "valence": 0.5}
    try:
        resp = requests.get(
            f"https://api.spotify.com/v1/audio-features/{track_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            bpm = int(round(data.get("tempo", 128)))
            if bpm < 100:
                bpm *= 2  # detect half-tempo
            return {
                "bpm": bpm,
                "energy": data.get("energy", 0.7),
                "danceability": data.get("danceability", 0.7),
                "valence": data.get("valence", 0.5),
            }
        return {"bpm": 128, "energy": 0.7, "danceability": 0.7, "valence": 0.5}
    except Exception:
        return {"bpm": 128, "energy": 0.7, "danceability": 0.7, "valence": 0.5}


def _update_env(key: str, value: str):
    """Update a key in .env file."""
    env_file = PROJECT_DIR / ".env"
    if not env_file.exists():
        return
    lines = env_file.read_text().splitlines()
    updated = False
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            updated = True
            break
    if not updated:
        lines.append(f"{key}={value}")
    env_file.write_text("\n".join(lines) + "\n")
