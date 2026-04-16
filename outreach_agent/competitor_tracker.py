"""Comparable artist monitoring for BTL Layer 3.

Tracks Spotify metrics for similar artists to detect growth spikes
and replicable tactics.

Usage:
  python3 competitor_tracker.py status    # Current tracking state
  python3 competitor_tracker.py update    # Fetch latest metrics (requires Spotify API)
  python3 competitor_tracker.py spikes    # Show artists with recent growth spikes
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from config import BASE_DIR

log = logging.getLogger(__name__)

TRACKING_FILE = Path(BASE_DIR).parent / "data" / "competitor_tracking.json"

SEED_ARTISTS = [
    {"name": "Anyma", "genre": "Melodic Techno", "reason": "Visual scale reference"},
    {"name": "Argy", "genre": "Tribal/Techno", "reason": "Tribal texture overlap"},
    {"name": "Agents Of Time", "genre": "Melodic Techno", "reason": "Similar production style"},
    {"name": "Colyn", "genre": "Melodic Techno", "reason": "Independent growth trajectory"},
    {"name": "Innellea", "genre": "Melodic Techno", "reason": "Similar visual aesthetic"},
]


def load_tracking() -> dict:
    if TRACKING_FILE.exists():
        data = json.loads(TRACKING_FILE.read_text())
        # Seed artists on first load if empty
        if not data.get("artists"):
            data["artists"] = SEED_ARTISTS
            save_tracking(data)
        return data
    return {"artists": SEED_ARTISTS, "snapshots": []}


def save_tracking(data: dict) -> None:
    TRACKING_FILE.write_text(json.dumps(data, indent=2))


def add_artist(name: str, genre: str = "", reason: str = "") -> None:
    data = load_tracking()
    if any(a["name"].lower() == name.lower() for a in data["artists"]):
        log.info("Already tracking %s", name)
        return
    data["artists"].append({"name": name, "genre": genre, "reason": reason})
    save_tracking(data)
    log.info("Added %s to competitor tracking", name)


def record_snapshot(artist_name: str, monthly_listeners: int) -> None:
    data = load_tracking()
    data["snapshots"].append({
        "artist": artist_name,
        "listeners": monthly_listeners,
        "date": datetime.utcnow().strftime("%Y-%m-%d"),
    })
    save_tracking(data)


def detect_spikes(threshold_pct: float = 20.0) -> list[dict]:
    data = load_tracking()
    spikes = []
    for artist in data["artists"]:
        snapshots = sorted(
            [s for s in data["snapshots"] if s["artist"] == artist["name"]],
            key=lambda s: s["date"],
        )
        if len(snapshots) < 2:
            continue
        current = snapshots[-1]["listeners"]
        previous = snapshots[-2]["listeners"]
        if previous > 0:
            growth = (current - previous) / previous * 100
            if growth >= threshold_pct:
                spikes.append({
                    "artist": artist["name"],
                    "previous": previous,
                    "current": current,
                    "growth_pct": round(growth, 1),
                    "date": snapshots[-1]["date"],
                })
    return spikes


def get_status() -> dict:
    data = load_tracking()
    return {
        "artists_tracked": len(data["artists"]),
        "total_snapshots": len(data["snapshots"]),
        "artists": [a["name"] for a in data["artists"]],
        "recent_spikes": detect_spikes(),
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 competitor_tracker.py [status|update|spikes]")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "status":
        print(json.dumps(get_status(), indent=2))
    elif cmd == "spikes":
        spikes = detect_spikes()
        if not spikes:
            print("No growth spikes detected.")
        for s in spikes:
            print(f"  {s['artist']}: {s['previous']} -> {s['current']} (+{s['growth_pct']}%)")
    elif cmd == "update":
        print("Spotify API update requires credentials — invoke via L3 discovery run.")
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
