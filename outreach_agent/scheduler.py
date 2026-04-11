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
    BOUNCE_RATE_WINDOW_DAYS,
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
    Check recent bounce rate against the configured threshold.
    Returns (True, status_msg) if safe to send, (False, reason) if paused.
    """
    import db as _db
    with _db.get_conn() as conn:
        cutoff = (datetime.now() - timedelta(days=BOUNCE_RATE_WINDOW_DAYS)).isoformat()
        row = conn.execute("""
            SELECT
                COUNT(*) FILTER (WHERE status IN ('sent','followup_sent','responded','won','closed')) AS total_sent,
                COUNT(*) FILTER (WHERE bounce = 'actual') AS actual_bounces
            FROM contacts
            WHERE date_sent >= ?
        """, (cutoff,)).fetchone()

    total_sent     = row["total_sent"] or 0
    actual_bounces = row["actual_bounces"] or 0

    if total_sent < 10:
        return True, f"Bounce check: not enough data ({total_sent} sends in window)"

    rate = actual_bounces / total_sent
    if rate > BOUNCE_RATE_LIMIT:
        return False, (
            f"BOUNCE RATE TOO HIGH: {actual_bounces}/{total_sent} = {rate:.1%} "
            f"over last {BOUNCE_RATE_WINDOW_DAYS}d (limit {BOUNCE_RATE_LIMIT:.0%}) — sends paused"
        )
    return True, f"Bounce rate OK: {actual_bounces}/{total_sent} = {rate:.1%} over last {BOUNCE_RATE_WINDOW_DAYS}d"


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
