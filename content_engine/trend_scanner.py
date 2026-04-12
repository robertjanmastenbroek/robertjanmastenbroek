"""
Module 1: Trend Scanner
Runs daily at 06:00 CET. Scrapes YouTube trending music + Spotify featured playlists,
synthesizes a Today's Brief via Claude CLI, saves to data/trend_brief/YYYY-MM-DD.json.
"""
import json
import logging
import os
import subprocess
from datetime import date as _date
from pathlib import Path

import requests

PROJECT_DIR = Path(__file__).parent.parent
logger = logging.getLogger(__name__)

YOUTUBE_TRENDING_URL = "https://www.googleapis.com/youtube/v3/videos"
SPOTIFY_FEATURED_URL = "https://api.spotify.com/v1/browse/featured-playlists"


def _find_claude() -> str:
    import glob
    candidates = [
        "/usr/local/bin/claude",
        "/opt/homebrew/bin/claude",
        os.path.expanduser("~/.local/bin/claude"),
        os.path.expanduser("~/Library/Application Support/Claude/claude"),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    # Claude Code desktop app — find latest installed version
    pattern = os.path.expanduser(
        "~/Library/Application Support/Claude/claude-code/*/claude.app/Contents/MacOS/claude"
    )
    hits = sorted(glob.glob(pattern))
    if hits:
        return hits[-1]
    return "claude"


def _call_claude(prompt: str) -> str:
    """Call Claude CLI. Returns stdout text."""
    claude = os.environ.get("CLAUDE_CLI_PATH", "") or _find_claude()
    result = subprocess.run(
        [claude, "--print", "--model", "claude-haiku-4-5-20251001",
         "--no-session-persistence",
         "--system-prompt", "You are a music trend analyst. Output only valid JSON, no commentary.",
         prompt],
        capture_output=True, text=True, timeout=120,
        cwd="/tmp",  # avoid loading project CLAUDE.md
    )
    if result.returncode != 0:
        raise RuntimeError(f"Claude CLI error: {result.stderr[:200]}")
    return result.stdout.strip()


def scrape_youtube_trending(api_key: str) -> list:
    """Return list of trending music video titles from YouTube."""
    if not api_key:
        return []
    try:
        resp = requests.get(YOUTUBE_TRENDING_URL, params={
            "part": "snippet",
            "chart": "mostPopular",
            "videoCategoryId": "10",  # Music
            "regionCode": "US",
            "maxResults": 20,
            "key": api_key,
        }, timeout=15)
        if resp.status_code != 200:
            return []
        items = resp.json().get("items", [])
        return [i["snippet"]["title"] for i in items]
    except Exception as e:
        logger.warning(f"YouTube trending scrape failed: {e}")
        return []


def scrape_spotify_featured(access_token: str) -> list:
    """Return featured playlist names + descriptions from Spotify."""
    if not access_token:
        return []
    try:
        token = access_token if access_token.startswith("Bearer") else f"Bearer {access_token}"
        resp = requests.get(SPOTIFY_FEATURED_URL, headers={"Authorization": token},
                            params={"limit": 20}, timeout=15)
        if resp.status_code != 200:
            return []
        playlists = resp.json().get("playlists", {}).get("items", [])
        return [f"{p['name']}: {p.get('description', '')}" for p in playlists]
    except Exception as e:
        logger.warning(f"Spotify featured scrape failed: {e}")
        return []


def synthesize_brief(date_str: str, youtube_data: list, spotify_data: list) -> "TrendBrief":
    """Use Claude CLI to synthesize a TrendBrief from scraped data."""
    from content_engine.types import TrendBrief

    prompt = f"""You are analyzing social media trends for a Melodic Techno / Tribal Psytrance artist (RJM / Holy Rave).
Brand: "Ancient Truth. Future Sound." — sacred geometry, tribal ritual, electronic energy.

Today's date: {date_str}

YouTube trending music titles:
{json.dumps(youtube_data[:15], indent=2) if youtube_data else "No data available"}

Spotify featured playlists:
{json.dumps(spotify_data[:10], indent=2) if spotify_data else "No data available"}

Based on these signals (or your general knowledge of current music trends if data is sparse), output a JSON object with EXACTLY these keys:
- top_visual_formats: list of 3 short strings describing visual styles gaining traction on short-form video
- dominant_emotion: one string (the emotional energy resonating most right now)
- oversaturated: one string describing what to AVOID (what everyone is doing)
- hook_pattern_of_day: one string describing the caption/text hook pattern gaining traction
- contrarian_gap: one string — what NOBODY in the techno/spiritual niche is doing = opportunity
- trend_confidence: float 0.0-1.0 (how confident you are, lower if data was sparse)

Output ONLY valid JSON, no commentary, no markdown fences."""

    raw = _call_claude(prompt)

    # Extract JSON robustly
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError(f"No JSON found in Claude response: {raw[:200]}")
    data = json.loads(raw[start:end])

    return TrendBrief(
        date=date_str,
        top_visual_formats=data["top_visual_formats"],
        dominant_emotion=data["dominant_emotion"],
        oversaturated=data["oversaturated"],
        hook_pattern_of_day=data["hook_pattern_of_day"],
        contrarian_gap=data["contrarian_gap"],
        trend_confidence=float(data["trend_confidence"]),
    )


def run(date_str: str = None) -> "TrendBrief":
    """Full trend scanner run. Returns and saves TrendBrief."""
    if date_str is None:
        date_str = _date.today().isoformat()

    youtube_key    = os.environ.get("YOUTUBE_API_KEY", "")
    spotify_token  = os.environ.get("SPOTIFY_ACCESS_TOKEN", "")

    logger.info(f"[trend_scanner] Scanning trends for {date_str}")
    youtube_data = scrape_youtube_trending(youtube_key)
    spotify_data = scrape_spotify_featured(spotify_token)

    logger.info(f"[trend_scanner] YouTube: {len(youtube_data)} titles, Spotify: {len(spotify_data)} playlists")

    brief = synthesize_brief(date_str, youtube_data, spotify_data)
    brief.save()
    logger.info(f"[trend_scanner] Brief saved — emotion={brief.dominant_emotion}, confidence={brief.trend_confidence:.2f}")
    return brief


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    b = run()
    print(json.dumps(b.__dict__, indent=2))
