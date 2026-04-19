"""
RJM Outreach Agent — Scheduler & Rate Limiter

Enforces:
  - Max 150 emails per calendar day
  - Active send window only (default 08:00–23:00 = 15 hrs active, 8 hr overnight break)
  - Minimum interval between sends (prevents burst sending)
  - Natural randomized delays to avoid robotic patterns
"""

import logging
import random
import time
from datetime import datetime, timedelta

from config import (
    MAX_EMAILS_PER_DAY,
    ACTIVE_HOUR_START,
    ACTIVE_HOUR_END,
    MIN_INTERVAL_SECONDS,
    MAX_INTERVAL_SECONDS,
    BATCH_SIZE,
    CRON_INTERVAL_MINUTES,
    BOUNCE_RATE_LIMIT,
)
from db import today_send_count, get_last_send_timestamp

log = logging.getLogger("outreach.scheduler")


def is_within_active_window() -> bool:
    """Return True if current local time is within the configured send window."""
    now = datetime.now()
    hour = now.hour
    return ACTIVE_HOUR_START <= hour < ACTIVE_HOUR_END


def seconds_until_window_opens() -> int:
    """How many seconds until the send window opens. 0 if already open."""
    if is_within_active_window():
        return 0
    now = datetime.now()
    if now.hour < ACTIVE_HOUR_START:
        opens_at = now.replace(hour=ACTIVE_HOUR_START, minute=0, second=0, microsecond=0)
    else:
        # Window has closed — opens tomorrow
        opens_at = (now + timedelta(days=1)).replace(
            hour=ACTIVE_HOUR_START, minute=0, second=0, microsecond=0
        )
    return max(0, int((opens_at - now).total_seconds()))


def remaining_quota_today() -> int:
    """How many more emails can be sent today within the daily cap."""
    sent = today_send_count()
    return max(0, MAX_EMAILS_PER_DAY - sent)


def seconds_since_last_send() -> float:
    """Seconds since the last email was sent (any day). Returns large number if never."""
    last_ts = get_last_send_timestamp()
    if not last_ts:
        return float("inf")
    try:
        last_dt = datetime.fromisoformat(last_ts)
        return (datetime.now() - last_dt).total_seconds()
    except (ValueError, TypeError):
        return float("inf")


def minimum_interval_satisfied() -> bool:
    """Return True if enough time has passed since the last send."""
    return seconds_since_last_send() >= MIN_INTERVAL_SECONDS


def random_interval() -> int:
    """
    Return a randomized delay in seconds between MIN and MAX intervals.
    Weighted toward the lower end to spread emails more evenly.
    """
    # Use triangular distribution — most sends near the lower bound
    # but with natural variance so pattern doesn't look robotic
    return int(random.triangular(MIN_INTERVAL_SECONDS, MAX_INTERVAL_SECONDS,
                                 MIN_INTERVAL_SECONDS * 1.5))


def compute_batch_size() -> int:
    """
    How many emails to send this cycle.
    Always attempts the full BATCH_SIZE unless quota or window prevents it.
    """
    if not is_within_active_window():
        return 0

    quota = remaining_quota_today()
    if quota <= 0:
        return 0

    batch = min(BATCH_SIZE, quota)
    log.debug("Batch size: %d (quota_remaining=%d)", batch, quota)
    return batch


def wait_for_interval():
    """Block until MIN_INTERVAL has passed since last send. Adds jitter."""
    elapsed = seconds_since_last_send()
    needed  = random_interval()

    log.info("wait_for_interval: elapsed=%.1fs needed=%ds will_sleep=%s",
             elapsed, needed, elapsed < needed)

    if elapsed < needed:
        wait = needed - elapsed
        log.info("Rate limit: waiting %.0f seconds before next send...", wait)
        time.sleep(wait)
    elif elapsed < MIN_INTERVAL_SECONDS:
        # Fallback: even if random_interval was already satisfied, enforce hard minimum
        wait = MIN_INTERVAL_SECONDS - elapsed
        log.info("Rate limit: enforcing hard minimum — waiting %.0f seconds...", wait)
        time.sleep(wait)


def bounce_rate_safe() -> tuple[bool, str]:
    """
    Check all-time bounce rate against the configured threshold.
    Returns (True, status_msg) if safe to send, (False, reason) if paused.

    Uses all-time data so that a clean history of thousands of sends isn't
    wiped out by a short window of noisy data. The denominator includes
    contacts currently in 'bounced' status so the rate is never inflated by
    excluding them from the total.
    """
    import db as _db
    with _db.get_conn() as conn:
        row = conn.execute("""
            SELECT
                COUNT(*) FILTER (WHERE date_sent IS NOT NULL) AS total_sent,
                COUNT(*) FILTER (WHERE bounce = 'actual') AS actual_bounces
            FROM contacts
        """).fetchone()

    total_sent     = row["total_sent"] or 0
    actual_bounces = row["actual_bounces"] or 0

    if total_sent < 10:
        return True, f"Bounce check: not enough data ({total_sent} all-time sends)"

    rate = actual_bounces / total_sent
    if rate > BOUNCE_RATE_LIMIT:
        return False, (
            f"BOUNCE RATE TOO HIGH: {actual_bounces}/{total_sent} = {rate:.1%} "
            f"all-time (limit {BOUNCE_RATE_LIMIT:.0%}) — sends paused"
        )
    return True, f"Bounce rate OK: {actual_bounces}/{total_sent} = {rate:.1%} all-time"


def _publish_rate_limit(reason: str, **payload) -> None:
    """Emit a rate_limit.hit event. Never raises — telemetry must not block sends."""
    try:
        import events  # local import avoids circular on cold start
        events.publish(
            "rate_limit.hit",
            source="outreach_agent.scheduler",
            payload={"reason": reason, **payload},
        )
    except Exception as exc:
        log.debug("rate_limit.hit publish skipped: %s", exc)


class SendWindow:
    """
    Context manager / guard that checks all rate limit conditions.

    Usage:
        window = SendWindow()
        if window.can_send:
            # send email
            window.record_send()
    """

    def __init__(self):
        self.in_window      = is_within_active_window()
        self.quota_left     = remaining_quota_today()
        self.interval_ok    = minimum_interval_satisfied()
        self.bounce_ok, self.bounce_status = bounce_rate_safe()
        self.can_send       = (
            self.in_window and self.quota_left > 0
            and self.interval_ok and self.bounce_ok
        )

        # Publish rate_limit.hit when any gate is blocking — so master agent
        # and rjm.py status can surface the reason instead of guessing why the
        # batch came back empty.
        if not self.can_send:
            if not self.bounce_ok:
                _publish_rate_limit("bounce_rate", status=self.bounce_status)
            elif not self.in_window:
                _publish_rate_limit(
                    "outside_window",
                    seconds_until_open=seconds_until_window_opens(),
                )
            elif self.quota_left <= 0:
                _publish_rate_limit("daily_quota", limit=MAX_EMAILS_PER_DAY)
            elif not self.interval_ok:
                wait = max(0, int(MIN_INTERVAL_SECONDS - seconds_since_last_send()))
                _publish_rate_limit("min_interval", wait_seconds=wait)

    def status(self) -> str:
        if not self.bounce_ok:
            return self.bounce_status
        if not self.in_window:
            secs = seconds_until_window_opens()
            h, m = divmod(secs // 60, 60)
            return f"Outside active window — opens in {h}h{m:02d}m"
        if self.quota_left <= 0:
            return f"Daily quota of {MAX_EMAILS_PER_DAY} reached — resuming tomorrow"
        if not self.interval_ok:
            wait = int(MIN_INTERVAL_SECONDS - seconds_since_last_send())
            return f"Minimum interval not met — wait {wait}s"
        return f"OK — {self.quota_left} emails remaining today | {self.bounce_status}"

    def record_send(self):
        """Call after a successful send to update internal state."""
        self.quota_left = max(0, self.quota_left - 1)
        self.can_send   = self.quota_left > 0 and self.in_window


def best_send_time(email: str) -> int:
    """
    Return the best hour (0-23 local) to send to this contact,
    based on when they have historically replied.

    Falls back to ACTIVE_HOUR_START + 2 (default: 10) if no reply history.
    """
    from datetime import datetime as _dt
    from collections import Counter
    from db import get_conn
    try:
        with get_conn() as conn:
            rows = conn.execute("""
                SELECT timestamp FROM email_log
                WHERE contact_email = ?
                  AND direction = 'received'
                ORDER BY timestamp DESC
                LIMIT 10
            """, (email,)).fetchall()
        if not rows:
            return ACTIVE_HOUR_START + 2
        hours = []
        for row in rows:
            try:
                hours.append(_dt.fromisoformat(row["timestamp"]).hour)
            except (ValueError, TypeError):
                continue
        if not hours:
            return ACTIVE_HOUR_START + 2
        best = Counter(hours).most_common(1)[0][0]
        return max(ACTIVE_HOUR_START, min(ACTIVE_HOUR_END - 1, best))
    except Exception:
        return ACTIVE_HOUR_START + 2
