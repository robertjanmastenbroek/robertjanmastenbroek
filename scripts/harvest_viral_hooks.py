"""harvest_viral_hooks.py — Pull short viral clips from Reddit into the bait library.

Targets the same content style as the existing transitionalhooks.com clips:
short (≤8s), surprising real-world moments, no clean audio needed since we
overlay music. Downloads from `v.redd.it`, re-encodes to 9:16 vertical
(1080x1920), and drops the result into `content/hooks/transitional/viral/`.

Skips clips already present (filename collision) and clips outside the
duration band. Usage:

    python3 scripts/harvest_viral_hooks.py            # default: ~50 new clips
    python3 scripts/harvest_viral_hooks.py --limit 20 # cap downloads
    python3 scripts/harvest_viral_hooks.py --dry-run  # list candidates only

Then run TransitionalManager.scan_for_new_clips() to register them.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent.parent
VIRAL_DIR = ROOT / "content" / "hooks" / "transitional" / "viral"

USER_AGENT = "rjm-content-engine:v1.0 (by /u/holyraveofficial)"

# Subs and time-windows tuned for the content style we want.
# Skewed toward wholesome / awe / surprise — Holy Rave brand can't carry
# politics, freakouts, or gore. `unexpected` and `holdmyredbull` keep
# their slot for kinetic energy, but the bulk is amazement / satisfaction.
SOURCES = [
    ("unexpected", "week"),
    ("unexpected", "month"),
    ("unexpected", "year"),
    ("nextfuckinglevel", "week"),
    ("nextfuckinglevel", "month"),
    ("nextfuckinglevel", "year"),
    ("BeAmazed", "week"),
    ("BeAmazed", "month"),
    ("BeAmazed", "year"),
    ("oddlysatisfying", "week"),
    ("oddlysatisfying", "month"),
    ("oddlysatisfying", "year"),
    ("holdmyredbull", "year"),
    ("interestingasfuck", "week"),
    ("interestingasfuck", "month"),
    ("interestingasfuck", "year"),
    ("MadeMeSmile", "week"),
    ("MadeMeSmile", "month"),
    ("AnimalsBeingDerps", "month"),
    ("AnimalsBeingDerps", "year"),
    ("Damnthatsinteresting", "week"),
    ("Damnthatsinteresting", "month"),
    ("Damnthatsinteresting", "year"),
    ("toptalent", "year"),
    ("HumansBeingBros", "year"),
    ("instant_regret", "year"),
    ("Whatcouldgowrong", "year"),
    ("therewasanattempt", "month"),
    ("ContagiousLaughter", "year"),
    ("blackmagicfuckery", "month"),
    ("blackmagicfuckery", "year"),
    ("aww", "week"),
    ("AnimalsBeingJerks", "year"),
    ("nonononoyes", "year"),
    ("MaybeMaybeMaybe", "month"),
]

# Title-level filter — proxy for visual content we don't want in the
# Holy Rave bait library. Skip if any of these tokens appear (case-insensitive).
BLOCKED_TITLE_TOKENS = {
    # Profanity / sexual
    "fuck", "fucking", "fucked", "shit", "bitch", "cock", "dick",
    "pussy", "tits", "boob", "porn", "nude", "naked", "sex", "horny",
    "cum", "blowjob",
    # Politics / inflammatory (wrong vibe for the brand)
    "trump", "biden", "putin", "israel", "palestine", "gaza",
    # Gore / death / abuse — bait shouldn't carry these
    "kill", "killed", "dead", "death", "murder", "shot", "shoot",
    "blood", "bloody", "stab", "rape", "abuse", "execution",
    # Substance imagery
    "drunk", "stoned", "drugs", "cocaine", "meth",
    # Generic freakout / negative-framed
    "freakout", "racist", "karen",
}

MIN_DURATION_S = 1.5
MAX_DURATION_S = 8.0
LIMIT_PER_SOURCE = 75
INTER_REQUEST_SLEEP_S = 1.2  # be polite — well under 60 req/min

OUT_W, OUT_H = 1080, 1920

logger = logging.getLogger("harvest")


def slugify(text: str, max_len: int = 50) -> str:
    """Reddit titles → safe filenames (alpha + dash, capitalized words)."""
    s = re.sub(r"[^A-Za-z0-9 ]", "", text)
    s = re.sub(r"\s+", "-", s.strip())
    if not s:
        s = "Clip"
    parts = s.split("-")
    parts = [p[:1].upper() + p[1:].lower() for p in parts if p]
    out = "-".join(parts)
    return out[:max_len].strip("-") or "Clip"


# Tokens that should match anywhere as substrings (catches bootfucked,
# fucking, fucked, holyshit, etc. — these are unambiguously profane).
_SUBSTRING_BLOCKED = {
    "fuck", "shit", "bitch", "cock", "dick", "pussy", "porn",
    "cum", "blowjob", "rape", "horny",
}


def title_is_blocked(title: str) -> bool:
    """Reject post titles that hint at off-brand visuals (profanity, gore, politics, etc.).

    Two-tier matching:
    - Profanity matches as substring (so 'fuck' catches 'bootfucked').
    - Other tokens match whole-word only (so 'kill' doesn't trigger on 'skills',
      and 'dead' doesn't trigger on 'deadlift').
    """
    low = title.lower()
    words = re.findall(r"[a-z]+", low)
    word_set = set(words)
    for blocked in BLOCKED_TITLE_TOKENS:
        if blocked in _SUBSTRING_BLOCKED:
            if any(blocked in w for w in words):
                return True
        else:
            if blocked in word_set:
                return True
    return False


def fetch_listing(sub: str, t: str, limit: int = 100) -> list[dict]:
    url = f"https://www.reddit.com/r/{sub}/top.json?limit={limit}&t={t}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        logger.warning(f"[{sub}/{t}] HTTP {e.code}")
        return []
    except Exception as e:
        logger.warning(f"[{sub}/{t}] fetch failed: {e}")
        return []
    return [c["data"] for c in data.get("data", {}).get("children", [])]


def reddit_video_url(post: dict) -> str | None:
    """Extract direct mp4 URL from a Reddit JSON post, or None if not a v.redd.it video."""
    media = post.get("media") or {}
    rv = media.get("reddit_video") if isinstance(media, dict) else None
    if not rv:
        # Crossposts wrap the video in crosspost_parent_list
        cp = post.get("crosspost_parent_list") or []
        if cp and isinstance(cp[0], dict):
            return reddit_video_url(cp[0])
        return None
    return rv.get("fallback_url")


def reddit_video_duration(post: dict) -> float | None:
    media = post.get("media") or {}
    rv = media.get("reddit_video") if isinstance(media, dict) else None
    if not rv:
        cp = post.get("crosspost_parent_list") or []
        if cp and isinstance(cp[0], dict):
            return reddit_video_duration(cp[0])
        return None
    d = rv.get("duration")
    return float(d) if d is not None else None


def download_url(url: str, dest: Path) -> bool:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp, dest.open("wb") as out:
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                out.write(chunk)
        return True
    except Exception as e:
        logger.warning(f"download failed {url}: {e}")
        if dest.exists():
            dest.unlink()
        return False


def reencode_vertical(src: Path, dst: Path) -> bool:
    """Crop-to-fill 9:16 then scale to 1080x1920. Strips audio (we overlay music)."""
    # Crop to 9:16 from center, then scale. Handles both wider and taller inputs.
    vf = (
        "crop='if(gt(iw/ih,9/16),ih*9/16,iw)':'if(gt(iw/ih,9/16),ih,iw*16/9)':"
        "(iw-out_w)/2:(ih-out_h)/2,"
        f"scale={OUT_W}:{OUT_H}:flags=lanczos,setsar=1"
    )
    cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-vf", vf,
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-preset", "fast", "-crf", "22",
        "-an",  # strip audio — track audio is overlaid downstream
        "-movflags", "+faststart",
        str(dst),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=120)
        if r.returncode != 0:
            logger.warning(f"ffmpeg failed for {src.name}: {r.stderr.decode()[-200:]}")
            return False
        return dst.exists() and dst.stat().st_size > 50_000
    except subprocess.TimeoutExpired:
        logger.warning(f"ffmpeg timeout for {src.name}")
        return False


def existing_slugs() -> set[str]:
    if not VIRAL_DIR.exists():
        return set()
    return {p.stem for p in VIRAL_DIR.glob("*.mp4")}


def harvest(limit: int, dry_run: bool) -> int:
    VIRAL_DIR.mkdir(parents=True, exist_ok=True)
    have = existing_slugs()
    logger.info(f"Existing viral clips: {len(have)}")

    saved = 0
    seen_urls: set[str] = set()

    for sub, t in SOURCES:
        if saved >= limit:
            break
        logger.info(f"--- r/{sub} top/{t} ---")
        time.sleep(INTER_REQUEST_SLEEP_S)
        posts = fetch_listing(sub, t, limit=LIMIT_PER_SOURCE)
        logger.info(f"  {len(posts)} posts returned")

        for post in posts:
            if saved >= limit:
                break
            if not post.get("is_video"):
                continue
            url = reddit_video_url(post)
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            duration = reddit_video_duration(post)
            if duration is None or not (MIN_DURATION_S <= duration <= MAX_DURATION_S):
                continue

            title = post.get("title", "Clip")
            if title_is_blocked(title):
                continue
            if post.get("over_18") or post.get("spoiler"):
                continue

            slug = slugify(title)
            if slug in have:
                continue

            logger.info(f"  candidate: {slug} ({duration:.1f}s)")
            if dry_run:
                saved += 1
                have.add(slug)
                continue

            tmp_path = VIRAL_DIR / f"_tmp_{slug}.mp4"
            final_path = VIRAL_DIR / f"{slug}.mp4"
            if final_path.exists():
                continue

            if not download_url(url, tmp_path):
                continue

            if not reencode_vertical(tmp_path, final_path):
                tmp_path.unlink(missing_ok=True)
                final_path.unlink(missing_ok=True)
                continue

            tmp_path.unlink(missing_ok=True)
            have.add(slug)
            saved += 1
            logger.info(f"  saved {final_path.name} ({final_path.stat().st_size//1024} KB)")
            time.sleep(0.4)  # gentle pacing on writes

    return saved


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
    )
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50,
                        help="Max number of clips to add this run (default 50)")
    parser.add_argument("--dry-run", action="store_true",
                        help="List candidates without downloading")
    args = parser.parse_args()

    n = harvest(args.limit, args.dry_run)
    print(f"\n✓ {'Would add' if args.dry_run else 'Added'} {n} viral clips → {VIRAL_DIR}")
    if not args.dry_run and n > 0:
        print("Next: python3 -c 'from content_engine.transitional_manager import TransitionalManager; TransitionalManager().scan_for_new_clips()'")
    return 0 if n > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
