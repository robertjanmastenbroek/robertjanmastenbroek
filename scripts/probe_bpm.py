#!/usr/bin/env python3
"""
probe_bpm.py — Read BPM + duration for every audio master on disk.

Strategy (in priority order per file):
  1. Check if title is in audio_engine.TRACK_BPMS (artist-verified — trust)
  2. Try mutagen for embedded BPM tag (ID3 TBPM, BWF IXML, etc.)
  3. If neither, run librosa beat-tracking as a last resort, BUT:
       - librosa is known to double-count psytrance BPMs (see note in
         audio_engine.py). Results are flagged ⚠️ for user verification.

No ffmpeg/PyAV — mutagen and librosa are both pure-Python. (librosa uses
numpy + scipy for beat tracking; it runs in-process but on a single file
for a few seconds, not the minutes of CPU an ffmpeg encode would pin.)

Output: markdown table of every audio file with BPM (source noted),
duration, filename. Sorted by BPM desc so Holy Rave candidates float to
the top.

Usage:
  python3 scripts/probe_bpm.py                    # all files
  python3 scripts/probe_bpm.py --no-librosa       # skip beat-tracking, mutagen-only
  python3 scripts/probe_bpm.py --only-unknown     # only probe files missing from TRACK_BPMS
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from content_engine.audio_engine import AUDIO_DIR, TRACK_BPMS


def _normalize(name: str) -> str:
    """Strip extension + common suffixes + make lowercase for matching."""
    stem = Path(name).stem.lower()
    # Strip author prefix if present
    stem = re.sub(r"^robert-jan mastenbroek\s*-\s*", "", stem)
    stem = re.sub(r"^robert-jan mastenbroek\s*&\s*lucid\s*-\s*", "", stem)
    # Strip _FINAL / _MASTER / _MASTER2 etc.
    stem = re.sub(r"_+(final|master|v2|v3|draft)\d*$", "", stem)
    stem = stem.replace("_", " ").strip()
    # Collapse multiple spaces
    stem = re.sub(r"\s+", " ", stem)
    return stem


def _probe_mutagen(path: Path) -> dict:
    """Return {'duration': float_sec, 'bpm': float_or_None, 'tags': dict}."""
    try:
        from mutagen import File as MutagenFile
    except ImportError:
        return {"duration": None, "bpm": None, "tags": {}, "error": "mutagen not installed"}
    try:
        mf = MutagenFile(str(path))
        if mf is None:
            return {"duration": None, "bpm": None, "tags": {}, "error": "could not parse"}
        duration = getattr(mf.info, "length", None) if hasattr(mf, "info") else None
        # Hunt for a BPM tag across common formats
        tags = dict(mf.tags) if mf.tags else {}
        bpm_value = None
        for key in ("BPM", "bpm", "TBPM", "TXXX:BPM", "----:com.apple.iTunes:BPM"):
            if key in tags:
                raw = tags[key]
                if hasattr(raw, "text"):
                    raw = raw.text
                if isinstance(raw, list) and raw:
                    raw = raw[0]
                try:
                    bpm_value = float(str(raw).strip())
                    break
                except (TypeError, ValueError):
                    continue
        return {"duration": duration, "bpm": bpm_value, "tags": tags, "error": None}
    except Exception as e:
        return {"duration": None, "bpm": None, "tags": {}, "error": str(e)}


def _probe_librosa(path: Path) -> float | None:
    """Beat-tracked BPM estimate. WARNING: doubles psytrance BPMs. Flag results."""
    try:
        import librosa  # type: ignore
        import numpy as np  # type: ignore
    except ImportError:
        return None
    try:
        y, sr = librosa.load(str(path), sr=22050, mono=True, duration=60)  # first 60s is plenty
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        # Newer librosa returns tempo as ndarray; older as scalar. Normalize:
        tempo_arr = np.atleast_1d(tempo)
        if tempo_arr.size == 0:
            return None
        return float(tempo_arr[0])
    except Exception as e:
        print(f"  ⚠ librosa failed on {path.name}: {e}", file=sys.stderr)
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-librosa", action="store_true", help="Skip librosa beat-tracking fallback")
    parser.add_argument("--only-unknown", action="store_true", help="Only files missing from TRACK_BPMS")
    args = parser.parse_args()

    if not AUDIO_DIR.exists():
        print(f"✗ Audio dir missing: {AUDIO_DIR}", file=sys.stderr)
        return 1

    files = sorted(
        p for p in AUDIO_DIR.iterdir()
        if p.suffix.lower() in (".wav", ".flac", ".mp3", ".aiff")
        and not p.name.startswith(".")
    )
    print(f"Scanning {len(files)} audio file(s) in {AUDIO_DIR}\n", file=sys.stderr)

    rows = []
    for p in files:
        canonical = _normalize(p.name)
        # Source 1: artist-verified TRACK_BPMS
        verified_bpm = None
        for key, bpm in TRACK_BPMS.items():
            if key in canonical or canonical in key:
                verified_bpm = bpm
                break
        if args.only_unknown and verified_bpm is not None:
            continue

        # Source 2: mutagen embedded tag + duration
        mut = _probe_mutagen(p)
        duration = mut["duration"]
        tag_bpm = mut["bpm"]

        # Source 3: librosa beat-track (only if no other source and allowed)
        lib_bpm = None
        if verified_bpm is None and tag_bpm is None and not args.no_librosa:
            print(f"  librosa probing {p.name}…", file=sys.stderr)
            lib_bpm = _probe_librosa(p)

        rows.append({
            "file":     p.name,
            "title":    canonical,
            "duration": duration,
            "verified": verified_bpm,
            "tag":      tag_bpm,
            "librosa":  lib_bpm,
        })

    # Sort: verified desc, then tag, then librosa, then alphabetic
    def _sort_key(r):
        for src in ("verified", "tag", "librosa"):
            if r[src] is not None:
                return (-r[src], r["title"])
        return (1, r["title"])
    rows.sort(key=_sort_key)

    # Markdown table output
    print("| Title                                     | BPM    | Source   | Dur   | File |")
    print("|-------------------------------------------|--------|----------|-------|------|")
    for r in rows:
        if r["verified"] is not None:
            bpm_str = f"{r['verified']}"
            source  = "verified"
        elif r["tag"] is not None:
            bpm_str = f"{r['tag']:.0f}"
            source  = "tag"
        elif r["librosa"] is not None:
            bpm_str = f"{r['librosa']:.0f}"
            source  = "librosa⚠"
        else:
            bpm_str = "—"
            source  = "unknown"
        dur_s   = f"{int(r['duration'] // 60)}:{int(r['duration'] % 60):02d}" if r["duration"] else "—"
        title   = r["title"][:40]
        fname   = r["file"][:40]
        print(f"| {title:<41} | {bpm_str:<6} | {source:<8} | {dur_s:<5} | {fname} |")

    return 0


if __name__ == "__main__":
    sys.exit(main())
