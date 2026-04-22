#!/usr/bin/env python3
"""
cleanup_shotstack.py — Retry/bulk purge of Shotstack-hosted render outputs.

We keep a log at data/youtube_longform/shotstack_renders.jsonl of every
render we've ever submitted (written by motion._log_shotstack_render).
This script iterates that log, for each render_id not yet marked
`deleted`, it calls:

  GET    https://api.shotstack.io/serve/{env}/assets/render/{render_id}
  DELETE https://api.shotstack.io/serve/{env}/assets/{asset_id}  (for each)

Why we need this:
  · Shotstack has no "list all stored assets" API. Without our own log,
    we'd have to read render IDs off the dashboard manually.
  · Deletes during the publish pipeline can fail (network glitch, timeout).
    This script is the retry path.

Going forward motion.stitch_full_track auto-deletes inline after verifying
the local MP4. Run this script when that fails or as a sanity sweep.

Usage:
    python3 scripts/cleanup_shotstack.py             # purge everything still live
    python3 scripts/cleanup_shotstack.py --dry-run   # show what WOULD be purged
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import requests
from content_engine.youtube_longform import config as cfg
from content_engine.youtube_longform import motion as motion_mod


logger = logging.getLogger("cleanup_shotstack")


def list_assets_for_render(
    render_id: str,
    env:       str,
    api_key:   str,
) -> list[dict]:
    """
    GET /serve/{env}/assets/render/{render_id} → list of asset objects.
    Returns [] on 404 (nothing stored) or error.
    """
    url = f"https://api.shotstack.io/serve/{env}/assets/render/{render_id}"
    try:
        r = requests.get(url, headers={"x-api-key": api_key}, timeout=20)
    except Exception as e:
        logger.warning("list_assets for %s raised: %s", render_id, e)
        return []
    if r.status_code == 404:
        return []
    if not r.ok:
        logger.warning(
            "list_assets for %s returned %d: %s",
            render_id, r.status_code, r.text[:300],
        )
        return []
    try:
        payload = r.json()
    except Exception:
        return []
    data = payload.get("data") or payload.get("response", {}).get("data") or []
    if isinstance(data, dict):
        data = [data]
    return data


def delete_asset(asset_id: str, env: str, api_key: str) -> bool:
    """DELETE /serve/{env}/assets/{asset_id} → True on 2xx/404."""
    url = f"https://api.shotstack.io/serve/{env}/assets/{asset_id}"
    try:
        r = requests.delete(url, headers={"x-api-key": api_key}, timeout=20)
    except Exception as e:
        logger.warning("delete %s/%s raised: %s", env, asset_id, e)
        return False
    if 200 <= r.status_code < 300 or r.status_code == 404:
        return True
    logger.warning("delete %s/%s returned %d: %s", env, asset_id, r.status_code, r.text[:200])
    return False


def load_pending_renders() -> list[dict]:
    """Return log rows where deleted=false."""
    if not motion_mod.SHOTSTACK_RENDER_LOG.exists():
        return []
    rows: list[dict] = []
    with open(motion_mod.SHOTSTACK_RENDER_LOG) as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not row.get("deleted"):
                rows.append(row)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="List renders that would be purged; do not call DELETE",
    )
    parser.add_argument(
        "--render-id", default=None,
        help="Purge only this single render_id (useful for one-off retries).",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if not cfg.SHOTSTACK_API_KEY:
        print("✗ SHOTSTACK_API_KEY not set", file=sys.stderr)
        return 1

    # Build work list
    if args.render_id:
        work = [{"render_id": args.render_id, "env": cfg.SHOTSTACK_ENV, "output_label": "(explicit)"}]
    else:
        work = load_pending_renders()

    if not work:
        print("No pending renders in shotstack_renders.jsonl.")
        print("Note: Shotstack has no list-all-assets endpoint. If the")
        print("dashboard shows stored output we don't know about, you'll")
        print("need to purge via the Shotstack Studio UI manually.")
        return 0

    print(f"Pending renders to purge: {len(work)}")
    print()

    purged = 0
    failed = 0
    for row in work:
        render_id = row["render_id"]
        env       = row.get("env", cfg.SHOTSTACK_ENV)
        label     = row.get("output_label", "(unknown)")

        assets = list_assets_for_render(render_id, env, cfg.SHOTSTACK_API_KEY)
        if not assets:
            print(f"  {render_id[:8]}…  ({env}, {label})  → 0 assets (already gone)")
            # Mark as deleted anyway — nothing to clean up
            if not args.dry_run:
                motion_mod._mark_shotstack_render_deleted(render_id)
            continue

        if args.dry_run:
            print(f"  {render_id[:8]}…  ({env}, {label})  → WOULD delete {len(assets)} asset(s)")
            continue

        all_ok = True
        for item in assets:
            attrs = item.get("attributes") or item
            aid = attrs.get("id") or item.get("id")
            if not aid:
                continue
            ok = delete_asset(aid, env, cfg.SHOTSTACK_API_KEY)
            if ok:
                print(f"    ✓ deleted {aid}")
            else:
                print(f"    ✗ failed  {aid}")
                all_ok = False

        if all_ok:
            motion_mod._mark_shotstack_render_deleted(render_id)
            purged += 1
        else:
            failed += 1

    print()
    print(f"Summary: {purged} purged, {failed} failed, {len(work) - purged - failed} empty.")
    if not args.dry_run and failed:
        print("Re-run to retry the failures.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
