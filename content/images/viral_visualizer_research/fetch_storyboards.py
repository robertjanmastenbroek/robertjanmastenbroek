#!/usr/bin/env python3
"""Fetch one storyboard sprite (the largest 'sb0' format) for each video
and save it next to the thumbnail as <videoid>.storyboard.jpg.

The info JSON lists formats with format_id like 'sb0', 'sb1', 'sb2'.
Each has a 'fragments' array — we pick the highest-resolution variant
and download the first fragment, which shows frames 0..N from the video.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.request import Request, urlopen


def pick_storyboard_url(info: dict) -> str | None:
    formats = info.get("formats", [])
    # storyboard formats have format_id starting with 'sb' and have
    # 'fragments' rather than a single url
    sb_formats = [f for f in formats if str(f.get("format_id", "")).startswith("sb")]
    if not sb_formats:
        return None
    # pick the highest-resolution one (biggest width * height)
    best = max(sb_formats, key=lambda f: (f.get("width") or 0) * (f.get("height") or 0))
    # use first fragment (shows first ~1/N of video as a 10x10 sprite grid)
    frags = best.get("fragments") or []
    if not frags:
        url = best.get("url")
        return url.replace("$M", "0") if url and "$M" in url else url
    return frags[0].get("url")


def fetch(url: str, out_path: Path) -> None:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 viral-research/1.0"})
    with urlopen(req, timeout=30) as r:
        out_path.write_bytes(r.read())


def main(root: Path) -> None:
    ok = 0
    fail = 0
    for bucket in ("bucket_psytrance", "bucket_organic"):
        bdir = root / bucket
        if not bdir.is_dir():
            continue
        for info_path in sorted(bdir.glob("*.info.json")):
            vid = info_path.stem.replace(".info", "")
            # info_path.stem removes one extension only (.json), leaving <id>.info
            if vid.endswith(".info"):
                vid = vid[:-5]
            out = bdir / f"{vid}.storyboard.jpg"
            if out.exists() and out.stat().st_size > 1000:
                print(f"skip (exists): {vid}")
                ok += 1
                continue
            try:
                info = json.loads(info_path.read_text())
                url = pick_storyboard_url(info)
                if not url:
                    print(f"no storyboard format: {vid}")
                    fail += 1
                    continue
                fetch(url, out)
                print(f"ok: {vid}  ({out.stat().st_size:,} bytes)")
                ok += 1
            except Exception as e:
                print(f"fail: {vid}  {e!r}")
                fail += 1
    print(f"\nDone: {ok} ok, {fail} failed")


if __name__ == "__main__":
    root = Path(__file__).parent
    main(root)
