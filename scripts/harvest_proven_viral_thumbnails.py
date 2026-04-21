#!/usr/bin/env python3
"""Harvest proven-viral YouTube thumbnails for the Holy Rave CLIP reference corpus.

Pipeline per channel:
  1. `yt-dlp --flat-playlist --dump-json <channel>/videos` -> JSON lines with view_count
  2. Filter: view_count >= MIN_VIEWS, title passes brand-reject keyword filter
  3. Download https://i.ytimg.com/vi/<id>/maxresdefault.jpg
  4. Verify filesize > 50KB (smaller means YT served a fallback placeholder)
  5. Append to manifest

Brand reject keywords intentionally conservative at the title level; visual filters
(Hindu deities, Mesoamerican pyramids, etc.) are applied downstream in CLIP scoring.
Here we catch only title-level giveaways (e.g. "Ayahuasca", "Ganesha", "Nebula").
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import date
from pathlib import Path
from typing import Iterable

ROOT = Path("/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/.claude/worktrees/hardcore-albattani-865ac9")
OUT = ROOT / "content" / "images" / "proven_viral"
MANIFEST = OUT / "manifest.json"
STATE = OUT / ".harvest_state.json"

MIN_VIEWS = 100_000           # relaxed from 500K — niche psytrance/ethnic-house channels max out around 500K-1M,
                              # so 100K is still genuinely viral and gets us to the 500 corpus target
TARGET_VIEWS = 1_000_000      # preferred floor — used for top-10 outlier selection
MIN_FILE_SIZE = 50_000        # under this = fallback placeholder
MAX_PER_CHANNEL_DEFAULT = 20  # cap per channel so we keep diversity

# Title-level reject keywords (lowercase). Visual filtering happens later via CLIP.
REJECT_KEYWORDS = [
    "ayahuasca", "ganesha", "shiva ", "kali ", "om mani",
    "buddha", "buddhist",
    "lofi", "lo-fi", "lo fi",
    "anime", "nightcore",
    "nebula", "galaxy", "astronaut", "cosmic voyage",
    "dmt", "mushroom",
    "mayan", "aztec", "feathered serpent",
    "christmas", "valentine",
    "asmr", "karaoke", "instrumental tutorial",
    "reaction", "tier list",
]

# Channels: (bucket, channel_url, channel_name, cap)
# Use https://www.youtube.com/channel/<ID>/videos for stability — @handles rot.
# EXPANSION PASS — floor lowered to 100K and caps raised on gold channels.
CHANNELS: list[tuple[str, str, str, int]] = [
    # Bucket A — 130 BPM organic / tribal / ethnic house / world fusion
    ("130_organic", "https://www.youtube.com/channel/UC1Tr6S-XLBk1NzNX1jErWMg/videos",  "Cafe De Anatolia",         60),
    # Cafe De Anatolia sub-labels — same visual identity, different mix curator
    ("130_organic", "https://www.youtube.com/channel/UCVfgJTxQOgyKboUckM1lvUA/videos",  "Cafe Anatolia MIX",        30),
    ("130_organic", "https://www.youtube.com/channel/UCzHWm4VO9mYZJ7eSZOnbHLA/videos",  "Cafe De Anatolia DEEP",    25),
    ("130_organic", "https://www.youtube.com/@CafeAnatolia/videos",                     "Cafe Anatolia",            25),
    # Individual Cafe De Anatolia co-founders
    ("130_organic", "https://www.youtube.com/channel/UC_pEO_ghlQpbH70zMccIvbw/videos",  "Rialians On Earth",        25),
    # Other major ethnic/organic labels and artists
    ("130_organic", "https://www.youtube.com/@sabonyc/videos",                          "Sol Selectas / Sabo",      25),
    ("130_organic", "https://www.youtube.com/c/Bedouin/videos",                         "Bedouin",                  25),
    ("130_organic", "https://www.youtube.com/channel/UCYzx8QoAiRb69fEgyBInAug/videos",  "Keinemusik",               30),
    ("130_organic", "https://www.youtube.com/channel/UChbNWfnY5xA-A57jmcals1w/videos",  "Acid Arab",                20),
    ("130_organic", "https://www.youtube.com/channel/UCpk60P3d3eA4gdot2jGYHhA/videos",  "All Day I Dream",          25),
    ("130_organic", "https://www.youtube.com/channel/UCbDgBFAketcO26wz-pR6OKA/videos",  "Anjunadeep",               35),
    ("130_organic", "https://www.youtube.com/channel/UCC7eKMxcVk1LZwzJlBdsVuQ/videos",  "Innervisions",             20),
    ("130_organic", "https://www.youtube.com/@diynamicmusic/videos",                    "Diynamic",                 20),
    ("130_organic", "https://www.youtube.com/channel/UCiv_kvaMssSr4UTM8IngW_Q/videos",  "Crosstown Rebels",         15),
    ("130_organic", "https://www.youtube.com/@HangMassive/videos",                      "Hang Massive",             30),
    ("130_organic", "https://www.youtube.com/channel/UCXzPTTX-VxZr5jiWxytz8hA/videos",  "Monolink",                 40),
    ("130_organic", "https://www.youtube.com/channel/UCIXRUzFIwt92kcQ7WVk0m4A/videos",  "Mind Against",             20),
    ("130_organic", "https://www.youtube.com/channel/UCUpHLJKLWCM7dA-6YfxBVWg/videos",  "Innellea",                 20),
    ("130_organic", "https://www.youtube.com/user/BeSVENDSEN/videos",                   "Be Svendsen",              15),
    # Bucket B — 140+ BPM tribal psytrance / Goa / melodic psy
    ("140_psytrance", "https://www.youtube.com/channel/UCsBSMQZsiLprSY6GqhI25Jw/videos",  "Astrix",                 60),
    ("140_psytrance", "https://www.youtube.com/channel/UChfawbJdUuHCTo1q-JG0OCg/videos",  "Ace Ventura",            30),
    ("140_psytrance", "https://www.youtube.com/channel/UCrktSiofrVs4UX396_fHViA/videos",  "Omiki",                  25),
    ("140_psytrance", "https://www.youtube.com/channel/UC0IDZXNx0sASs8Z1UQ-HBog/videos",  "Symphonix",              20),
    ("140_psytrance", "https://www.youtube.com/channel/UCp2F1GDhk-ZGk_ZISqd3ABQ/videos",  "Vini Vici",              40),
    ("140_psytrance", "https://www.youtube.com/channel/UCrzie1GJaav42CyVbA8cVFw/videos",  "Ranji",                  15),
    ("140_psytrance", "https://www.youtube.com/channel/UCsgcXbjl1R2oPuVUhBn3lQQ/videos",  "Ozora Festival",         35),
    ("140_psytrance", "https://www.youtube.com/user/BoomWebTv/videos",                    "Boom Festival",          30),
    ("140_psytrance", "https://www.youtube.com/channel/UCK3vmdxfttWSGm4P-31laEQ/videos",  "Iboga Records",          25),
    ("140_psytrance", "https://www.youtube.com/@TesseracTstudio/videos",                  "TesseracTstudio",        25),
    ("140_psytrance", "https://www.youtube.com/@BlueTunesRecords/videos",                 "Blue Tunes Records",     20),
    ("140_psytrance", "https://www.youtube.com/@TristanOfficial/videos",                  "Tristan",                20),
    ("140_psytrance", "https://www.youtube.com/channel/UCt-Zwf8TON73Wtz21R8DgAg/videos",  "Nano Records",           20),
    ("140_psytrance", "https://www.youtube.com/user/TipWorldRecords/videos",              "TIP World Records",      15),
    ("140_psytrance", "https://www.youtube.com/user/digitalomreleases/videos",            "Digital Om",             15),
    # Top-up pass — added to close the 250 gap in Bucket B
    ("140_psytrance", "https://www.youtube.com/channel/UCrbvoMC0zUvPL8vjswhLOSw/videos",  "Infected Mushroom",       35),
    ("140_psytrance", "https://www.youtube.com/channel/UCsfo7TDmSy2Ed3EY_JQDTtg/videos",  "Avalon",                  15),
    ("140_psytrance", "https://www.youtube.com/channel/UCEJR7drcG-SARz8ItFYSp6A/videos",  "Indian Spirit Festival",  20),
    ("140_psytrance", "https://www.youtube.com/channel/UCFhL1VCPIqnqCM_TXcB0AOQ/videos",  "Berg",                    15),
]


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return s


def title_passes_filter(title: str) -> bool:
    t = title.lower()
    for kw in REJECT_KEYWORDS:
        if kw in t:
            return False
    return True


def dump_channel(channel_url: str) -> list[dict]:
    """Dump channel videos with yt-dlp flat mode. Returns list of dicts with id/title/view_count."""
    try:
        result = subprocess.run(
            [
                "yt-dlp",
                "--flat-playlist",
                "--dump-json",
                "--playlist-end", "200",      # scan up to 200 videos per channel
                "--no-warnings",
                "--ignore-errors",
                channel_url,
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        print(f"  TIMEOUT on {channel_url}", file=sys.stderr)
        return []
    videos = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            v = json.loads(line)
        except json.JSONDecodeError:
            continue
        vid = v.get("id")
        title = v.get("title") or ""
        views = v.get("view_count") or 0
        if not vid:
            continue
        videos.append({"id": vid, "title": title, "view_count": int(views)})
    return videos


def download_thumbnail(video_id: str, out_path: Path) -> tuple[bool, int]:
    """Try maxresdefault, fall back to hqdefault if file too small. Returns (ok, size)."""
    urls = [
        f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg",
        f"https://i.ytimg.com/vi/{video_id}/sddefault.jpg",
        f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
    ]
    for url in urls:
        try:
            subprocess.run(
                ["curl", "-sL", "-o", str(out_path), url],
                capture_output=True, timeout=30,
            )
        except subprocess.TimeoutExpired:
            continue
        if not out_path.exists():
            continue
        size = out_path.stat().st_size
        if size >= MIN_FILE_SIZE:
            # Verify it's actually a JPEG
            try:
                head = out_path.read_bytes()[:3]
                if head[:2] == b"\xff\xd8":
                    return True, size
            except Exception:
                pass
        out_path.unlink(missing_ok=True)
    return False, 0


def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"seen_ids": [], "channel_dumps": {}, "counters": {"130_organic": 0, "140_psytrance": 0}}


def save_state(state: dict) -> None:
    STATE.write_text(json.dumps(state, indent=2))


def load_manifest() -> list:
    if MANIFEST.exists():
        try:
            return json.loads(MANIFEST.read_text())
        except json.JSONDecodeError:
            return []
    return []


def save_manifest(entries: list) -> None:
    MANIFEST.write_text(json.dumps(entries, indent=2))


def harvest() -> None:
    state = load_state()
    manifest = load_manifest()
    seen = set(state["seen_ids"])
    counters = state["counters"]
    stats = {
        "candidates_considered": 0,
        "rejected_title_filter": 0,
        "rejected_low_views": 0,
        "rejected_fallback_image": 0,
        "already_seen": 0,
        "accepted": 0,
    }
    by_channel = {}
    by_bucket = {"130_organic": 0, "140_psytrance": 0}

    for idx, (bucket, url, channel_name, cap) in enumerate(CHANNELS):
        print(f"\n[{idx+1}/{len(CHANNELS)}] {channel_name} ({bucket}) -> {url}")
        if url in state["channel_dumps"]:
            videos = state["channel_dumps"][url]
            print(f"  cached dump: {len(videos)} videos")
        else:
            videos = dump_channel(url)
            state["channel_dumps"][url] = videos
            save_state(state)
            print(f"  fresh dump: {len(videos)} videos")
            time.sleep(1)

        # sort by view_count descending
        videos.sort(key=lambda v: v["view_count"], reverse=True)
        accepted_for_channel = 0
        slug = slugify(channel_name)
        bucket_dir = OUT / f"bucket_{bucket}"
        bucket_dir.mkdir(parents=True, exist_ok=True)

        for v in videos:
            if accepted_for_channel >= cap:
                break
            stats["candidates_considered"] += 1
            if v["id"] in seen:
                stats["already_seen"] += 1
                continue
            if v["view_count"] < MIN_VIEWS:
                stats["rejected_low_views"] += 1
                continue
            if not title_passes_filter(v["title"]):
                stats["rejected_title_filter"] += 1
                continue
            counters[bucket] += 1
            idx_str = f"{counters[bucket]:04d}"
            descriptor = slugify(v["title"])[:60] or "untitled"
            filename = f"{idx_str}_{slug}_{descriptor}.jpg"
            out_path = bucket_dir / filename
            ok, size = download_thumbnail(v["id"], out_path)
            if not ok:
                stats["rejected_fallback_image"] += 1
                counters[bucket] -= 1
                continue
            entry = {
                "bucket": bucket,
                "filename": filename,
                "source_url": f"https://youtu.be/{v['id']}",
                "channel": channel_name,
                "view_count_estimate": v["view_count"],
                "title": v["title"],
                "description": "",
                "date_added": str(date.today()),
                "file_size_bytes": size,
            }
            manifest.append(entry)
            seen.add(v["id"])
            stats["accepted"] += 1
            by_channel[channel_name] = by_channel.get(channel_name, 0) + 1
            by_bucket[bucket] += 1
            accepted_for_channel += 1
            if stats["accepted"] % 10 == 0:
                state["seen_ids"] = list(seen)
                state["counters"] = counters
                save_state(state)
                save_manifest(manifest)
        print(f"  accepted {accepted_for_channel} from {channel_name} (bucket totals: {by_bucket})")

        state["seen_ids"] = list(seen)
        state["counters"] = counters
        save_state(state)
        save_manifest(manifest)

    print("\n=== FINAL STATS ===")
    print(json.dumps(stats, indent=2))
    print(f"By bucket: {by_bucket}")
    print(f"By channel: {json.dumps(by_channel, indent=2)}")
    print(f"Manifest entries: {len(manifest)}")


if __name__ == "__main__":
    harvest()
