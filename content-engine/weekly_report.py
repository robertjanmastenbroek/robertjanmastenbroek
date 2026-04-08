"""
Weekly analytics sync + performance report.

Run every Sunday night (or any time) to:
  1. Pull Buffer analytics for all connected profiles
  2. Write performance scores back to the hook database (best hooks rise to top)
  3. Print a human-readable weekly summary
  4. Log progress toward 1M Spotify monthly listeners

Usage:
  python3 weekly_report.py
  python3 weekly_report.py --no-sync   # just print report, skip Buffer sync
  python3 weekly_report.py --dry-run   # simulate without writing to DB

Automate with cron (every Sunday 23:00 CET):
  0 23 * * 0  cd /path/to/content-engine && python3 weekly_report.py >> ../logs/weekly.log 2>&1
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)

CC_ROOT  = Path(os.environ.get('RJM_ROOT',
                '~/Documents/Robert-Jan Mastenbroek Command Centre')).expanduser()
LOG_DIR  = CC_ROOT / 'logs'


def _load_env():
    env_path = CC_ROOT / '.env'
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, _, v = line.partition('=')
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and not os.environ.get(k):
            os.environ[k] = v


_load_env()


def sync_buffer_to_hook_db(dry_run: bool = False) -> dict:
    """
    Pull Buffer sent-post analytics, match each post text to a hook in the
    hook_database, and update performance_score.

    Returns summary dict: {synced, skipped, errors, top_hooks}
    """
    try:
        import publisher_buffer
        import hook_database
    except ImportError as e:
        logger.error(f"Import failed: {e}")
        return {'synced': 0, 'skipped': 0, 'errors': 1, 'top_hooks': []}

    # Validate token before pulling
    if not publisher_buffer.validate_token():
        logger.error("Buffer token invalid — skipping sync. Run: python3 buffer_auth.py")
        return {'synced': 0, 'skipped': 0, 'errors': 1, 'top_hooks': []}

    profiles = publisher_buffer.get_profiles()
    if not profiles:
        logger.warning("No Buffer profiles found")
        return {'synced': 0, 'skipped': 0, 'errors': 0, 'top_hooks': []}

    records = publisher_buffer.get_analytics_summary(profiles)
    logger.info(f"Pulled {len(records)} analytics records from Buffer")

    synced = skipped = errors = 0

    for rec in records:
        if rec.get('views', 0) < 50:
            skipped += 1
            continue  # Not enough data for meaningful signal

        post_text = rec.get('text', '')[:400]
        if not post_text:
            skipped += 1
            continue

        try:
            import sqlite3
            conn = sqlite3.connect(str(hook_database.DB_PATH))
            # Case-insensitive substring match: post text contains the hook
            cursor = conn.execute(
                "SELECT hook_text FROM hooks "
                "WHERE UPPER(?) LIKE '%' || UPPER(hook_text) || '%' "
                "ORDER BY LENGTH(hook_text) DESC LIMIT 1",
                (post_text,)
            )
            row = cursor.fetchone()
            conn.close()

            if row:
                if not dry_run:
                    hook_database.record_performance(
                        hook_text=row[0],
                        views=rec['views'],
                        likes=rec['likes'],
                        shares=rec['shares'],
                    )
                synced += 1
                logger.debug(f"Synced: '{row[0][:50]}' "
                             f"(views={rec['views']}, likes={rec['likes']}, shares={rec['shares']})")
            else:
                skipped += 1
        except Exception as e:
            logger.warning(f"Hook match error: {e}")
            errors += 1

    # Retrieve top hooks post-sync
    top_hooks = []
    try:
        top_hooks = hook_database.get_best_hooks(limit=5)
    except Exception:
        pass

    return {'synced': synced, 'skipped': skipped, 'errors': errors, 'top_hooks': top_hooks}


def print_report(sync_result: dict, no_sync: bool = False) -> None:
    """Print a human-readable weekly performance report."""
    now = datetime.now(timezone.utc)
    week_str = now.strftime('Week of %Y-%m-%d')

    print()
    print("╔══════════════════════════════════════════════════════════════╗")
    print(f"  HOLY RAVE WEEKLY REPORT — {week_str}")
    print("  Goal: 1,000,000 Spotify monthly listeners")
    print("╚══════════════════════════════════════════════════════════════╝")
    print()

    # ── Buffer sync results ────────────────────────────────────────────────
    if not no_sync:
        print("━━━ BUFFER ANALYTICS SYNC ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print(f"  Posts matched to hook DB:  {sync_result['synced']}")
        print(f"  Posts skipped (<50 views): {sync_result['skipped']}")
        print(f"  Errors:                    {sync_result['errors']}")
        print()

    # ── Top performing hooks ───────────────────────────────────────────────
    top = sync_result.get('top_hooks', [])
    if top:
        print("━━━ TOP HOOKS BY PERFORMANCE SCORE ━━━━━━━━━━━━━━━━━━━━━━━━")
        for i, h in enumerate(top, 1):
            score = h.get('performance_score') or 0.0
            track = h.get('filename_pattern', '?')
            bucket = h.get('bucket', '?')
            text = h.get('hook_text', '')[:60]
            print(f"  {i}. [{track} / {bucket}] score={score:.1f}")
            print(f"     \"{text}\"")
        print()

    # ── Content library stats ──────────────────────────────────────────────
    try:
        import content_library
        stats = content_library.library_stats()
        history = content_library.get_run_history(limit=7)

        print("━━━ CONTENT LIBRARY ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print(f"  Active rotation: {len(stats['active_songs'])} songs")
        for s in stats['active_songs']:
            last = s['last_used'][:10] if s['last_used'] != 'never' else 'never'
            print(f"    {s['name']:<45}  used {s['use_count']}x  last: {last}")
        print()
        print(f"  Videos in library: {stats['videos']} total  ({stats['videos_unused']} unused)")
        print(f"  Total runs: {stats['runs_total']}")
        print()

        if history:
            print("  Last 7 runs:")
            print(f"  {'Date':<22} {'Song':<35} {'Clips':>5}  Status")
            print(f"  {'─'*65}")
            for r in history:
                status = "posted" if r['posted'] else "clips only"
                print(f"  {r['date'][:19]:<22} {r['song']:<35} {r['n_videos']:>5}  {status}")
        print()
    except Exception as e:
        logger.warning(f"Could not load content library stats: {e}")

    # ── Hook database stats ────────────────────────────────────────────────
    try:
        import hook_database
        import sqlite3
        conn = sqlite3.connect(str(hook_database.DB_PATH))
        n_tracks = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
        n_hooks  = conn.execute("SELECT COUNT(*) FROM hooks").fetchone()[0]
        n_scored = conn.execute(
            "SELECT COUNT(*) FROM hooks WHERE performance_score IS NOT NULL"
        ).fetchone()[0]
        conn.close()
        print("━━━ HOOK DATABASE ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print(f"  Tracks: {n_tracks}  |  Hooks: {n_hooks}  |  Scored: {n_scored}")
        pct = (n_scored / max(n_hooks, 1)) * 100
        print(f"  Coverage: {pct:.1f}% of hooks have performance data")
        print()
    except Exception as e:
        logger.warning(f"Could not load hook DB stats: {e}")

    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("  Next actions:")
    print("  1. Review top hooks above — double down on what's working")
    print("  2. Check Buffer for platform breakdown (TikTok vs IG vs YT)")
    print("  3. Run: python3 daily_run.py  for today's content")
    print()
    print("  All glory to Jesus.")
    print()


def main():
    parser = argparse.ArgumentParser(
        description='Holy Rave weekly analytics sync + report'
    )
    parser.add_argument('--no-sync', action='store_true',
                        help='Skip Buffer sync, just print report')
    parser.add_argument('--dry-run', action='store_true',
                        help='Pull analytics but don\'t write back to hook DB')
    args = parser.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    if args.no_sync:
        sync_result = {'synced': 0, 'skipped': 0, 'errors': 0, 'top_hooks': []}
        try:
            import hook_database
            sync_result['top_hooks'] = hook_database.get_best_hooks(limit=5)
        except Exception:
            pass
    else:
        logger.info("Pulling Buffer analytics...")
        sync_result = sync_buffer_to_hook_db(dry_run=args.dry_run)
        if args.dry_run:
            logger.info("Dry run — hook scores NOT written to DB")
        else:
            logger.info(f"Sync complete: {sync_result['synced']} hooks updated")

    print_report(sync_result, no_sync=args.no_sync)


if __name__ == '__main__':
    main()
