"""
Performance learning system — logs content output, accepts metrics,
analyzes patterns, and feeds strategy notes back into content generation.

Cycle:
1. Engine generates content → log_batch() records what was made
2. You post the content and observe results
3. You (or a future scraper) call record_performance() with the numbers
4. Next time engine runs, get_strategy_notes() returns learnings → injected into Claude prompt
5. Claude generates hooks/captions informed by what's actually working

Drive storage: rjm_performance_log.json in the output folder.
"""

import io
import json
import logging
import os
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

LOG_FILENAME = 'rjm_performance_log.json'

# Minimum entries before we have enough data to surface learnings
MIN_ENTRIES_FOR_STRATEGY = 5


class PerformanceLearner:
    def __init__(self, drive_service=None, output_folder_id: str = None):
        """
        drive_service: authenticated Google Drive service object (from drive.get_service())
        output_folder_id: Drive folder ID where the log JSON is stored
        """
        self._svc = drive_service
        self._folder_id = output_folder_id
        self._log: dict = self._empty_log()
        self._file_id: Optional[str] = None  # Drive file ID of the log, once found/created

        # Attempt to load existing log from Drive on startup
        if self._svc and self._folder_id:
            try:
                self.load_from_drive()
            except Exception as e:
                logger.warning(f"Could not load performance log from Drive: {e} — starting fresh")

    # ── Drive sync ────────────────────────────────────────────────────────────

    def _find_log_file_id(self) -> Optional[str]:
        """Search the output folder for the log file. Returns Drive file ID or None."""
        query = (
            f"'{self._folder_id}' in parents and "
            f"name = '{LOG_FILENAME}' and "
            f"trashed = false"
        )
        results = self._svc.files().list(
            q=query,
            fields="files(id, name)",
            pageSize=1
        ).execute()
        files = results.get('files', [])
        return files[0]['id'] if files else None

    def load_from_drive(self):
        """Download and parse the performance log from Drive. No-op if file not found."""
        if not self._svc or not self._folder_id:
            return

        file_id = self._find_log_file_id()
        if not file_id:
            logger.info("No existing performance log on Drive — will create on first save")
            return

        self._file_id = file_id
        request = self._svc.files().get_media(fileId=file_id)
        buf = io.BytesIO()

        from googleapiclient.http import MediaIoBaseDownload
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()

        raw = buf.getvalue().decode('utf-8')
        try:
            data = json.loads(raw)
            if data.get('version') == 1:
                self._log = data
                batches = len(self._log.get('batches', []))
                metrics = len(self._log.get('metrics', []))
                logger.info(f"Performance log loaded: {batches} batches, {metrics} metric entries")
            else:
                logger.warning("Performance log version mismatch — starting fresh")
                self._log = self._empty_log()
        except json.JSONDecodeError as e:
            logger.error(f"Performance log JSON invalid: {e} — starting fresh")
            self._log = self._empty_log()

    def save_to_drive(self):
        """Serialise the log and upload (create or update) on Drive."""
        if not self._svc or not self._folder_id:
            logger.debug("No Drive service configured — performance log not saved")
            return

        content = json.dumps(self._log, indent=2, ensure_ascii=False)
        encoded = content.encode('utf-8')
        buf = io.BytesIO(encoded)

        from googleapiclient.http import MediaIoBaseUpload
        media = MediaIoBaseUpload(buf, mimetype='application/json', resumable=False)

        if self._file_id:
            # Update existing file
            self._svc.files().update(
                fileId=self._file_id,
                media_body=media
            ).execute()
            logger.info(f"Performance log updated on Drive ({LOG_FILENAME})")
        else:
            # Create new file
            file_metadata = {
                'name': LOG_FILENAME,
                'parents': [self._folder_id],
                'mimeType': 'application/json',
            }
            result = self._svc.files().create(
                body=file_metadata,
                media_body=media,
                fields='id'
            ).execute()
            self._file_id = result['id']
            logger.info(f"Performance log created on Drive ({LOG_FILENAME}, id: {self._file_id})")

    # ── Logging ───────────────────────────────────────────────────────────────

    def log_batch(self, filename: str, bucket: str, content_type: str,
                  hooks: dict, clip_lengths: list):
        """
        Record a batch of clips that was generated from a single source video.

        filename     : source video filename (e.g. 'reach_sunset_clip.mp4')
        bucket       : content bucket (e.g. 'reach', 'follow', 'spotify')
        content_type : type of footage (e.g. 'event', 'studio', 'bts')
        hooks        : {15: 'hook text', 30: 'hook text', 60: 'hook text'}
        clip_lengths : [15, 30, 60]

        Returns the batch ID (str uuid4).
        """
        batch_id = str(uuid.uuid4())
        # Normalise hook keys to strings for JSON compatibility
        hooks_str = {str(k): v for k, v in (hooks or {}).items()}

        entry = {
            'id': batch_id,
            'filename': filename,
            'bucket': bucket,
            'content_type': content_type,
            'clip_lengths': clip_lengths,
            'hooks': hooks_str,
            'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        }
        self._log['batches'].append(entry)
        logger.info(f"Logged batch {batch_id} for {filename}")
        self.save_to_drive()
        return batch_id

    # ── Metric recording ──────────────────────────────────────────────────────

    def record_performance(self, filename: str, clip_length: int, platform: str,
                           views: int = 0, likes: int = 0, shares: int = 0,
                           comments: int = 0, follows_gained: int = 0,
                           streams_gained: int = 0):
        """
        Record observed performance numbers for a specific clip on a specific platform.

        filename    : source video filename (matches what was passed to log_batch)
        clip_length : 15, 30, or 60
        platform    : 'tiktok', 'instagram', 'youtube', etc.
        """
        # Find the batch ID for this filename
        batch_id = None
        for batch in reversed(self._log['batches']):  # Most recent first
            if batch['filename'] == filename:
                batch_id = batch['id']
                break

        if not batch_id:
            logger.warning(
                f"record_performance: no batch found for '{filename}' — "
                f"creating orphan metric entry"
            )

        entry = {
            'batch_id': batch_id,
            'filename': filename,
            'clip_length': clip_length,
            'platform': platform,
            'views': views,
            'likes': likes,
            'shares': shares,
            'comments': comments,
            'follows_gained': follows_gained,
            'streams_gained': streams_gained,
            'recorded_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        }
        self._log['metrics'].append(entry)
        logger.info(
            f"Recorded performance: {filename} {clip_length}s on {platform} — "
            f"{views:,} views"
        )
        self.save_to_drive()

    # ── Analysis ──────────────────────────────────────────────────────────────

    def _analyze_patterns(self) -> dict:
        """
        Crunch the metrics log to find what's working.

        Returns a dict with:
          best_bucket             : str
          best_content_type       : str
          top_hooks               : list of (hook_text, avg_views, sample_count)
          worst_hooks             : list of (hook_text, avg_views, sample_count)
          best_platform           : str
          engagement_rate_by_bucket : dict {bucket: avg_engagement_rate}
          avg_views_by_bucket     : dict {bucket: avg_views}
          avg_views_by_content_type: dict {content_type: avg_views}
          follows_per_1k_by_platform: dict {platform: follows_per_1000_views}
          streams_per_1k_by_platform: dict {platform: streams_per_1000_views}
          total_entries           : int
        """
        metrics = self._log.get('metrics', [])
        batches = {b['id']: b for b in self._log.get('batches', [])}

        if not metrics:
            return {}

        # ── Aggregation buckets ──────────────────────────────────────────────
        views_by_bucket: dict[str, list] = defaultdict(list)
        engagement_by_bucket: dict[str, list] = defaultdict(list)
        views_by_content_type: dict[str, list] = defaultdict(list)
        views_by_platform: dict[str, list] = defaultdict(list)
        follows_by_platform: dict[str, list] = defaultdict(list)
        streams_by_platform: dict[str, list] = defaultdict(list)
        hook_views: dict[str, list] = defaultdict(list)  # hook_text -> [views]

        for m in metrics:
            views = m.get('views', 0)
            likes = m.get('likes', 0)
            shares = m.get('shares', 0)
            comments = m.get('comments', 0)
            follows = m.get('follows_gained', 0)
            streams = m.get('streams_gained', 0)
            platform = m.get('platform', 'unknown')
            clip_len = str(m.get('clip_length', ''))

            # Engagement rate = (likes + shares + comments) / views, if views > 0
            eng_rate = (likes + shares + comments) / views if views > 0 else 0.0

            # Look up batch metadata
            batch = batches.get(m.get('batch_id', ''), {})
            bucket = batch.get('bucket', 'unknown')
            content_type = batch.get('content_type', 'unknown')
            hook = batch.get('hooks', {}).get(clip_len, '')

            views_by_bucket[bucket].append(views)
            engagement_by_bucket[bucket].append(eng_rate)
            views_by_content_type[content_type].append(views)
            views_by_platform[platform].append(views)

            if follows > 0:
                follows_per_1k = (follows / views * 1000) if views > 0 else 0
                follows_by_platform[platform].append(follows_per_1k)

            if streams > 0:
                streams_per_1k = (streams / views * 1000) if views > 0 else 0
                streams_by_platform[platform].append(streams_per_1k)

            if hook:
                hook_views[hook].append(views)

        def avg(lst):
            return sum(lst) / len(lst) if lst else 0.0

        # ── Best bucket ──────────────────────────────────────────────────────
        avg_views_by_bucket = {b: avg(v) for b, v in views_by_bucket.items()}
        best_bucket = max(avg_views_by_bucket, key=avg_views_by_bucket.get) if avg_views_by_bucket else 'unknown'

        # ── Best content type ────────────────────────────────────────────────
        avg_views_by_ct = {ct: avg(v) for ct, v in views_by_content_type.items()}
        best_content_type = max(avg_views_by_ct, key=avg_views_by_ct.get) if avg_views_by_ct else 'unknown'

        # ── Engagement rate by bucket ────────────────────────────────────────
        eng_rate_by_bucket = {b: avg(v) for b, v in engagement_by_bucket.items()}

        # ── Hook analysis ────────────────────────────────────────────────────
        hook_avg = [(h, avg(v), len(v)) for h, v in hook_views.items()]
        hook_avg.sort(key=lambda x: x[1], reverse=True)
        top_hooks = hook_avg[:5]
        worst_hooks = sorted(hook_avg, key=lambda x: x[1])[:3]

        # ── Best platform ────────────────────────────────────────────────────
        avg_views_by_platform = {p: avg(v) for p, v in views_by_platform.items()}
        best_platform = max(avg_views_by_platform, key=avg_views_by_platform.get) if avg_views_by_platform else 'unknown'

        # ── Follows + streams per 1k ─────────────────────────────────────────
        follows_per_1k = {p: avg(v) for p, v in follows_by_platform.items()}
        streams_per_1k = {p: avg(v) for p, v in streams_by_platform.items()}

        return {
            'best_bucket': best_bucket,
            'best_content_type': best_content_type,
            'top_hooks': top_hooks,
            'worst_hooks': worst_hooks,
            'best_platform': best_platform,
            'engagement_rate_by_bucket': eng_rate_by_bucket,
            'avg_views_by_bucket': avg_views_by_bucket,
            'avg_views_by_content_type': avg_views_by_ct,
            'follows_per_1k_by_platform': follows_per_1k,
            'streams_per_1k_by_platform': streams_per_1k,
            'total_entries': len(metrics),
        }

    def get_strategy_notes(self) -> str:
        """
        Return a formatted multi-line string of performance learnings to inject
        into the Claude content-generation prompt.

        Returns an empty string if fewer than MIN_ENTRIES_FOR_STRATEGY metric
        entries exist (not enough data to draw conclusions yet).
        """
        metrics = self._log.get('metrics', [])
        if len(metrics) < MIN_ENTRIES_FOR_STRATEGY:
            return ''

        p = self._analyze_patterns()
        if not p:
            return ''

        n = p['total_entries']
        lines = [f"PERFORMANCE LEARNINGS FROM {n} POSTS:"]

        # ── Best bucket ──────────────────────────────────────────────────────
        best_b = p['best_bucket']
        avg_views_b = p['avg_views_by_bucket']
        if avg_views_b:
            best_avg = avg_views_b.get(best_b, 0)
            other_avgs = [v for b, v in avg_views_b.items() if b != best_b]
            if other_avgs:
                comparison_avg = sum(other_avgs) / len(other_avgs)
                lines.append(
                    f"- Best performing bucket: {best_b.upper()} "
                    f"(avg {best_avg:,.0f} views vs {comparison_avg:,.0f} for other buckets)"
                )
            else:
                lines.append(
                    f"- Best performing bucket: {best_b.upper()} "
                    f"(avg {best_avg:,.0f} views)"
                )

        # ── Best content type ────────────────────────────────────────────────
        best_ct = p['best_content_type']
        eng_by_bucket = p['engagement_rate_by_bucket']
        best_eng = eng_by_bucket.get(best_b, 0) * 100  # as percentage
        if best_ct != 'unknown':
            lines.append(
                f"- Best content type: {best_ct} footage "
                f"(avg engagement rate: {best_eng:.1f}%)"
            )

        # ── Top hooks ────────────────────────────────────────────────────────
        top_hooks = p.get('top_hooks', [])
        if top_hooks:
            hook_strs = ', '.join(
                f'"{h}" ({v:,.0f} views)' for h, v, _ in top_hooks[:2]
            )
            lines.append(f"- Top performing hooks: {hook_strs}")

        # ── Worst hooks ──────────────────────────────────────────────────────
        worst_hooks = p.get('worst_hooks', [])
        # Only surface worst hooks if they have enough samples (>= 2) and are clearly bad
        bad_hooks = [(h, v, c) for h, v, c in worst_hooks if c >= 2]
        if bad_hooks:
            hook_str = bad_hooks[0]
            lines.append(
                f"- Avoid hooks like: \"{hook_str[0]}\" "
                f"(avg {hook_str[1]:,.0f} views — too generic)"
            )

        # ── Best platform ────────────────────────────────────────────────────
        best_p = p['best_platform']
        follows_per_1k = p.get('follows_per_1k_by_platform', {})
        if best_p != 'unknown' and follows_per_1k:
            best_follows = follows_per_1k.get(best_p, 0)
            other_follows = [v for pl, v in follows_per_1k.items() if pl != best_p and v > 0]
            if other_follows and best_follows > 0:
                ratio = best_follows / (sum(other_follows) / len(other_follows))
                lines.append(
                    f"- Best platform: {best_p.title()} drives "
                    f"{ratio:.1f}x more follows than other platforms for {best_b} content"
                )
            elif best_p != 'unknown':
                lines.append(f"- Best platform for reach: {best_p.title()}")

        # ── Spotify streams insight ──────────────────────────────────────────
        streams_per_1k = p.get('streams_per_1k_by_platform', {})
        # Aggregate across all platforms to get a global spotify-bucket insight
        batches = {b['id']: b for b in self._log.get('batches', [])}
        spotify_bucket_streams = []
        for m in self._log.get('metrics', []):
            batch = batches.get(m.get('batch_id', ''), {})
            if batch.get('bucket', '') == 'spotify':
                views = m.get('views', 0)
                streams = m.get('streams_gained', 0)
                if views > 0 and streams > 0:
                    spotify_bucket_streams.append(streams / views * 1000)
        if spotify_bucket_streams:
            avg_sps = sum(spotify_bucket_streams) / len(spotify_bucket_streams)
            lines.append(
                f"- Spotify bucket drives avg {avg_sps:.0f} streams per 1,000 views"
            )

        lines.append("Apply these patterns when writing new hooks and captions.")

        return '\n'.join(lines)

    def generate_report(self) -> str:
        """
        Return a human-readable markdown report with all-time stats and top performers.
        """
        batches = self._log.get('batches', [])
        metrics = self._log.get('metrics', [])

        if not batches and not metrics:
            return "# RJM Content Performance Report\n\n_No data recorded yet._\n"

        p = self._analyze_patterns() if metrics else {}

        lines = [
            "# RJM Content Performance Report",
            f"_Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_",
            "",
            "## Overview",
            f"- Total batches logged: {len(batches)}",
            f"- Total metric entries: {len(metrics)}",
            "",
        ]

        if not metrics:
            lines.append("_No performance metrics recorded yet. Post content and call `record_performance()` to start learning._")
            return '\n'.join(lines)

        # ── Views by bucket ──────────────────────────────────────────────────
        lines += ["## Average Views by Bucket", ""]
        avg_vb = p.get('avg_views_by_bucket', {})
        for bucket, avg_v in sorted(avg_vb.items(), key=lambda x: -x[1]):
            marker = " ← BEST" if bucket == p.get('best_bucket') else ""
            lines.append(f"- **{bucket}**: {avg_v:,.0f} avg views{marker}")
        lines.append("")

        # ── Engagement by bucket ─────────────────────────────────────────────
        lines += ["## Engagement Rate by Bucket", ""]
        eng_b = p.get('engagement_rate_by_bucket', {})
        for bucket, rate in sorted(eng_b.items(), key=lambda x: -x[1]):
            lines.append(f"- **{bucket}**: {rate * 100:.1f}%")
        lines.append("")

        # ── Views by content type ────────────────────────────────────────────
        lines += ["## Average Views by Content Type", ""]
        avg_ct = p.get('avg_views_by_content_type', {})
        for ct, avg_v in sorted(avg_ct.items(), key=lambda x: -x[1]):
            marker = " ← BEST" if ct == p.get('best_content_type') else ""
            lines.append(f"- **{ct}**: {avg_v:,.0f} avg views{marker}")
        lines.append("")

        # ── Top hooks ────────────────────────────────────────────────────────
        top_hooks = p.get('top_hooks', [])
        if top_hooks:
            lines += ["## Top Performing Hooks", ""]
            for i, (hook, avg_v, count) in enumerate(top_hooks, 1):
                lines.append(f"{i}. \"{hook}\" — {avg_v:,.0f} avg views ({count} sample{'s' if count != 1 else ''})")
            lines.append("")

        # ── Hooks to avoid ───────────────────────────────────────────────────
        worst_hooks = p.get('worst_hooks', [])
        if worst_hooks:
            lines += ["## Hooks to Reconsider", ""]
            for hook, avg_v, count in worst_hooks:
                lines.append(f"- \"{hook}\" — {avg_v:,.0f} avg views ({count} sample{'s' if count != 1 else ''})")
            lines.append("")

        # ── Platform breakdown ───────────────────────────────────────────────
        platform_views = defaultdict(list)
        platform_follows = defaultdict(list)
        platform_streams = defaultdict(list)
        for m in metrics:
            pl = m.get('platform', 'unknown')
            v = m.get('views', 0)
            f = m.get('follows_gained', 0)
            s = m.get('streams_gained', 0)
            platform_views[pl].append(v)
            if v > 0:
                if f > 0:
                    platform_follows[pl].append(f / v * 1000)
                if s > 0:
                    platform_streams[pl].append(s / v * 1000)

        def avg(lst):
            return sum(lst) / len(lst) if lst else 0.0

        lines += ["## Platform Performance", ""]
        for pl, vl in sorted(platform_views.items(), key=lambda x: -avg(x[1])):
            avg_v = avg(vl)
            follows_str = (
                f", {avg(platform_follows[pl]):.1f} follows/1k views"
                if platform_follows[pl] else ""
            )
            streams_str = (
                f", {avg(platform_streams[pl]):.1f} streams/1k views"
                if platform_streams[pl] else ""
            )
            marker = " ← BEST" if pl == p.get('best_platform') else ""
            lines.append(f"- **{pl.title()}**: {avg_v:,.0f} avg views{follows_str}{streams_str}{marker}")
        lines.append("")

        # ── Recent batches table ─────────────────────────────────────────────
        lines += ["## Recent Batches (last 10)", ""]
        lines.append("| Filename | Bucket | Type | Clips | Date |")
        lines.append("|----------|--------|------|-------|------|")
        for b in reversed(batches[-10:]):
            clips = ', '.join(str(c) + 's' for c in b.get('clip_lengths', []))
            ts = b.get('timestamp', '')[:10]
            lines.append(
                f"| {b['filename']} | {b['bucket']} | {b['content_type']} | {clips} | {ts} |"
            )
        lines.append("")

        return '\n'.join(lines)

    # ── Local disk persistence (no Google Drive required) ─────────────────────

    def load_from_disk(self, path):
        """Load performance log from a local JSON file. No-op if file absent."""
        from pathlib import Path
        p = Path(path)
        if not p.exists():
            logger.info(f"No performance log at {p} — starting fresh")
            return
        try:
            data = json.loads(p.read_text(encoding='utf-8'))
            if data.get('version') == 1:
                self._log = data
                logger.info(f"Performance log loaded: {len(self._log['batches'])} batches, "
                            f"{len(self._log['metrics'])} metrics")
            else:
                logger.warning("Performance log version mismatch — starting fresh")
        except Exception as e:
            logger.warning(f"Could not load performance log: {e} — starting fresh")

    def save_to_disk(self, path):
        """Persist performance log to a local JSON file."""
        from pathlib import Path
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self._log, indent=2, ensure_ascii=False), encoding='utf-8')
        logger.debug(f"Performance log saved → {p}")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _empty_log(self) -> dict:
        """Return a fresh, empty log structure."""
        return {
            'version': 1,
            'batches': [],
            'metrics': [],
        }
