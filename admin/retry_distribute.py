#!/usr/bin/env python3
"""Retry distribution of already-rendered clips with loaded .env creds.

Uses hand-curated, brand-gate-compliant captions (not re-generated via
Claude CLI) so the retry path completes in ~3 minutes instead of ~25.
The captions here are the ones already validated in the earlier
distribution attempt (see data/failed_posts.json), re-used verbatim.

IDEMPOTENCY: this script refuses to run if today's registry already has
successful posts — pass --force to override. This is a hard lesson from
2026-04-16 when 3 retry invocations each posted a fresh batch.
"""
import argparse
import json
import sys
from pathlib import Path

# Make project root importable no matter where this script is launched from
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "outreach_agent"))

import content_engine.pipeline  # noqa: E402 — triggers .env load

from content_engine.distributor import distribute_all  # noqa: E402

# --- CLI flags (idempotency override) ---
_parser = argparse.ArgumentParser(description=__doc__)
_parser.add_argument("--force", action="store_true",
                     help="Bypass the per-day idempotency guard")
_args, _ = _parser.parse_known_args()

DATE = "2026-04-16"

# --- Idempotency guard: abort if today's registry already has successes ---
_registry = PROJECT_ROOT / "data" / "performance" / f"{DATE}_posts.json"
if _registry.exists() and not _args.force:
    try:
        _existing = json.loads(_registry.read_text())
        _successes = [r for r in _existing
                      if r.get("success") is True or (r.get("post_id") or "").strip()]
        if _successes:
            print(
                f"[retry] IDEMPOTENCY GUARD: {DATE} already has {len(_successes)} "
                f"successful posts. Refusing to run. Pass --force to override, or "
                f"delete {_registry} if the underlying posts have been taken down.",
                flush=True,
            )
            sys.exit(0)
    except Exception as _exc:
        print(f"[retry] could not read registry ({_exc}) — proceeding.", flush=True)
TRACK = "fire in our hands"
SPOTIFY_URL = "https://open.spotify.com/artist/2Seaafm5k1hAuCkpdq7yds"

HOOKS = {
    "transitional": "Count with me: 5... 4... 3... heat takes what we can't keep",
    "emotional":    "Told 140 BPM minimum for rupture. Built fire at 129. The room came apart anyway.",
    "performance":  "Fire, found at 129.",
}
DURATIONS = {"transitional": 22, "emotional": 7, "performance": 28}

# Pre-validated captions from the earlier pipeline run (brand-gate passed).
# Per-platform variants stay tight to each surface's conventions:
#   - IG/FB feed: 5 lines incl. 5 hashtags
#   - YouTube:    same structure, slightly longer CTA
#   - TikTok:     no hashtags block; single CTA line ends with track link
#   - Stories:    1-2 lines, no hashtags (stickers carry the link)
HASHTAGS_RJM = "#holyrave #RobertJanMastenbroek #melodictechno #tribalpsytrance #fire"

CAPTIONS_BY_FMT = {
    "transitional": {
        "instagram": (
            "Five. Four. Three. Heat. Fire In Our Hands by Robert-Jan Mastenbroek\n"
            "129 BPM melodic techno · tribal psytrance · Holy Rave\n"
            "What burns, we release. Spotify: " + SPOTIFY_URL + "\n"
            + HASHTAGS_RJM
        ),
        "facebook": (
            "Five. Four. Three. Heat. Fire In Our Hands by Robert-Jan Mastenbroek\n"
            "129 BPM melodic techno · tribal psytrance · Holy Rave\n"
            "What burns, we release. Spotify: " + SPOTIFY_URL + "\n"
            + HASHTAGS_RJM
        ),
        "youtube": (
            "Fire In Our Hands · Robert-Jan Mastenbroek\n"
            "129 BPM melodic techno / tribal psytrance · Holy Rave\n"
            "Count with me: 5, 4, 3 — heat takes what we can't keep. "
            "Full track on Spotify: " + SPOTIFY_URL + "\n"
            + HASHTAGS_RJM
        ),
        "tiktok": (
            "5... 4... 3... heat takes what we can't keep 🔥\n"
            "Fire In Our Hands — 129 BPM, out now.\n"
            "Full track: " + SPOTIFY_URL
        ),
        "instagram_story": "Fire In Our Hands — out now. Tap to listen on Spotify.",
        "facebook_story":  "Fire In Our Hands — out now. Tap to listen on Spotify.",
    },
    "emotional": {
        "instagram": (
            "Fire In Our Hands · Robert-Jan Mastenbroek\n"
            "129 BPM tribal techno · Holy Rave\n"
            "They said 140 minimum. We built fire at 129. The room came apart anyway.\n"
            "Listen on Spotify: " + SPOTIFY_URL + "\n"
            "#holyrave #RobertJanMastenbroek #melodictechno #tribalpsytrance #fireinhands"
        ),
        "facebook": (
            "Fire In Our Hands · Robert-Jan Mastenbroek\n"
            "129 BPM tribal techno · Holy Rave\n"
            "They said 140 minimum. We built fire at 129. The room came apart anyway.\n"
            "Listen on Spotify: " + SPOTIFY_URL + "\n"
            "#holyrave #RobertJanMastenbroek #melodictechno #tribalpsytrance #fireinhands"
        ),
        "youtube": (
            "Fire In Our Hands · Robert-Jan Mastenbroek\n"
            "129 BPM tribal techno · Holy Rave\n"
            "They told me 140 BPM was the minimum for a room to come apart. "
            "I built fire at 129. The room came apart anyway.\n"
            "Listen on Spotify: " + SPOTIFY_URL + "\n"
            "#holyrave #RobertJanMastenbroek #melodictechno #tribalpsytrance #fireinhands"
        ),
        "tiktok": (
            "They said 140 BPM minimum. Built fire at 129. The room came apart anyway.\n"
            "Fire In Our Hands — Spotify: " + SPOTIFY_URL
        ),
        "instagram_story": "Fire at 129 BPM. Still breaks rooms. Listen on Spotify.",
        "facebook_story":  "Fire at 129 BPM. Still breaks rooms. Listen on Spotify.",
    },
    "performance": {
        "instagram": (
            "Fire In Our Hands · Robert-Jan Mastenbroek\n"
            "129 BPM melodic techno · Holy Rave\n"
            "Your hands at 0:52. Watch them become the fire. Stream on Spotify — link in bio\n\n"
            "#holyrave #RobertJanMastenbroek #melodictechno #fireinyourhands #psytrance"
        ),
        "facebook": (
            "Fire In Our Hands · Robert-Jan Mastenbroek\n"
            "129 BPM melodic techno · Holy Rave\n"
            "Your hands at 0:52. Watch them become the fire. Full track on Spotify: "
            + SPOTIFY_URL + "\n\n"
            "#holyrave #RobertJanMastenbroek #melodictechno #fireinyourhands #psytrance"
        ),
        "youtube": (
            "Fire In Our Hands · Robert-Jan Mastenbroek\n"
            "129 BPM melodic techno · Holy Rave\n"
            "Your hands at 0:52 — watch them become the fire. "
            "Full track on Spotify: " + SPOTIFY_URL + "\n"
            "#holyrave #RobertJanMastenbroek #melodictechno #fireinyourhands #psytrance"
        ),
        "tiktok": (
            "Watch the hands at 0:52. They become the fire.\n"
            "Fire In Our Hands — Spotify: " + SPOTIFY_URL
        ),
        "instagram_story": "Your hands become the fire at 0:52. Tap for Spotify.",
        "facebook_story":  "Your hands become the fire at 0:52. Tap for Spotify.",
    },
}

base = Path(PROJECT_ROOT / "content" / "output" / DATE)

clips = []
for idx, fmt in enumerate(["transitional", "emotional", "performance"]):
    stem = f"{fmt}_fire_in_our_hands"
    path = str(base / f"{stem}.mp4")
    story = str(base / f"{stem}_story.mp4")
    caps = CAPTIONS_BY_FMT[fmt]
    clips.append({
        "clip_index": idx,
        "format_type": fmt,
        "hook_mechanism": "manual-retry",
        "hook_template_id": "manual-retry",
        "hook_sub_mode": "COST",
        "hook_text": HOOKS[fmt],
        "caption": caps["instagram"],
        "caption_by_platform": caps,
        "track_title": TRACK,
        "clip_length": DURATIONS[fmt],
        "visual_type": "b_roll",
        "transitional_category": "",
        "transitional_file": "",
        "path": path,
        "story_path": story,
        "spotify_url": SPOTIFY_URL,
    })

print(f"Built {len(clips)} clip specs. Distributing to 6 platforms each...\n", flush=True)
results = distribute_all(clips)

print("\n=== DISTRIBUTION RESULTS ===", flush=True)
successes = 0
failures = 0
for r in results:
    status = "OK  " if r.get("success") else "FAIL"
    via = r.get("via", "native") or "native"
    err = (r.get("error", "") or "")[:100] if not r.get("success") else ""
    post_id = (r.get("post_id", "") or "")[:24] if r.get("success") else ""
    print(
        f"  {status} clip{r.get('clip_index')} "
        f"{r.get('platform', ''):<18} via={via:<18} {post_id or err}",
        flush=True,
    )
    if r.get("success"):
        successes += 1
    else:
        failures += 1
print(f"\n=== {successes} ok / {failures} failed of {len(results)} ===", flush=True)
sys.exit(0 if failures == 0 else 1)
