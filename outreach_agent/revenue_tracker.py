"""
Boil the Lake — Revenue Tracker

Tracks donations (Stripe), allocates a fixed percentage to the growth budget,
records spend, and gates auto-spend against per-action / daily / reserve caps.

Schema (created by btl_db.init_btl_tables):
    growth_budget(id, date, type, amount, source, channel,
                  experiment_id, note, created_at)

Row-type semantics:
    'donation'   — gross inflow (positive amount, e.g. Stripe charge)
    'allocation' — portion routed to growth budget (positive, BTL_DONATION_ALLOCATION_PCT × donation)
    'spend'      — outflow against allocation (stored as negative amount)
    'refund'     — reserved for Stripe refund webhooks (positive offset to a donation)

available_balance = total_allocated − total_spent

Auto-spend gates (must ALL pass):
    1. amount ≤ BTL_AUTO_SPEND_MAX_EUR              (per-action ceiling)
    2. balance − amount ≥ BTL_RESERVE_MIN_EUR       (cash reserve floor)
    3. daily_spend + amount ≤ min(BTL_DAILY_SPEND_CAP_EUR,
                                  balance × BTL_DAILY_SPEND_CAP_PCT)
"""

import logging
from datetime import date
from typing import Optional

import db
import config

log = logging.getLogger("outreach.revenue_tracker")


# ─── Internal helpers ───────────────────────────────────────────────────────
def _today() -> str:
    return date.today().isoformat()


def _insert(
    conn,
    *,
    type_: str,
    amount: float,
    source: str = "",
    channel: str = "",
    experiment_id: str = "",
    note: str = "",
    on_date: Optional[str] = None,
) -> int:
    cur = conn.execute(
        """INSERT INTO growth_budget
           (date, type, amount, source, channel, experiment_id, note)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (on_date or _today(), type_, amount, source, channel, experiment_id, note),
    )
    return cur.lastrowid


# ─── Public API ─────────────────────────────────────────────────────────────
def record_donation(amount: float, source: str = "", note: str = "") -> dict:
    """
    Insert a donation row plus an allocation row at BTL_DONATION_ALLOCATION_PCT.

    Returns: {"donation_id", "allocation_id", "donation_amount", "allocated_amount"}
    """
    amount = round(float(amount), 2)
    allocated = round(amount * config.BTL_DONATION_ALLOCATION_PCT, 2)
    with db.get_conn() as conn:
        donation_id = _insert(
            conn, type_="donation", amount=amount, source=source, note=note
        )
        allocation_id = _insert(
            conn,
            type_="allocation",
            amount=allocated,
            source=source,
            note=f"auto-alloc {int(config.BTL_DONATION_ALLOCATION_PCT * 100)}% of donation",
        )
    log.info(
        "donation recorded: %.2f EUR (source=%s) → allocated %.2f EUR",
        amount, source, allocated,
    )
    return {
        "donation_id": donation_id,
        "allocation_id": allocation_id,
        "donation_amount": amount,
        "allocated_amount": allocated,
    }


def record_spend(
    amount: float,
    channel: str = "",
    experiment_id: str = "",
    note: str = "",
) -> int:
    """Insert a spend row. Stored as negative regardless of caller sign."""
    signed = -abs(round(float(amount), 2))
    with db.get_conn() as conn:
        spend_id = _insert(
            conn,
            type_="spend",
            amount=signed,
            channel=channel,
            experiment_id=experiment_id,
            note=note,
        )
    log.info(
        "spend recorded: %.2f EUR (channel=%s exp=%s)",
        signed, channel, experiment_id,
    )
    return spend_id


def get_budget_summary() -> dict:
    """
    Aggregate the growth_budget ledger.

    Returns:
        {
          "total_donations":   sum of type='donation' amounts,
          "total_allocated":   sum of type='allocation' amounts,
          "total_spent":       absolute value of sum of type='spend' amounts,
          "available_balance": total_allocated − total_spent,
        }
    All values rounded to 2 decimals.
    """
    with db.get_conn() as conn:
        row = conn.execute(
            """SELECT
                 COALESCE(SUM(CASE WHEN type='donation'   THEN amount END), 0) AS donations,
                 COALESCE(SUM(CASE WHEN type='allocation' THEN amount END), 0) AS allocated,
                 COALESCE(SUM(CASE WHEN type='spend'      THEN amount END), 0) AS spent_signed
               FROM growth_budget"""
        ).fetchone()
    donations = round(float(row["donations"]), 2)
    allocated = round(float(row["allocated"]), 2)
    spent = round(abs(float(row["spent_signed"])), 2)
    return {
        "total_donations":   donations,
        "total_allocated":   allocated,
        "total_spent":       spent,
        "available_balance": round(allocated - spent, 2),
    }


def get_daily_spend(on_date: Optional[str] = None) -> float:
    """Sum of |amount| for type='spend' on the given date (default: today)."""
    target = on_date or _today()
    with db.get_conn() as conn:
        row = conn.execute(
            """SELECT COALESCE(SUM(amount), 0) AS s
               FROM growth_budget
               WHERE type='spend' AND date=?""",
            (target,),
        ).fetchone()
    return round(abs(float(row["s"])), 2)


def _check_spend_gates(balance: float, daily: float, amount: float) -> Optional[str]:
    """Shared gate logic for can_auto_spend + try_auto_spend.

    Returns ``None`` if all caps hold, otherwise a short reason string.
    """
    if amount <= 0:
        return "amount must be > 0"
    if amount > config.BTL_AUTO_SPEND_MAX_EUR:
        return (
            f"amount {amount:.2f} > per-action max "
            f"{config.BTL_AUTO_SPEND_MAX_EUR:.2f}"
        )
    if balance - amount < config.BTL_RESERVE_MIN_EUR:
        return (
            f"balance {balance:.2f} − {amount:.2f} < reserve "
            f"{config.BTL_RESERVE_MIN_EUR:.2f}"
        )
    pct_cap = balance * config.BTL_DAILY_SPEND_CAP_PCT
    daily_cap = min(config.BTL_DAILY_SPEND_CAP_EUR, pct_cap)
    if daily + amount > daily_cap + 1e-9:
        return (
            f"daily {daily:.2f} + {amount:.2f} > cap {daily_cap:.2f} "
            f"(eur={config.BTL_DAILY_SPEND_CAP_EUR:.2f}, pct={pct_cap:.2f})"
        )
    return None


def can_auto_spend(amount: float) -> bool:
    """
    Read-only advisory check: would all caps hold for a spend of ``amount``?

    Auto-spend gates (must ALL pass):
      1. amount ≤ BTL_AUTO_SPEND_MAX_EUR
      2. balance − amount ≥ BTL_RESERVE_MIN_EUR
      3. daily_spend + amount ≤ min(BTL_DAILY_SPEND_CAP_EUR,
                                    balance × BTL_DAILY_SPEND_CAP_PCT)

    **Caveat:** the result is advisory only. By the time you act on it,
    another caller may have spent against the same budget. Production
    callers that intend to spend MUST use ``try_auto_spend`` instead —
    it atomically re-checks the gates while holding a write lock and
    records the spend in the same transaction.
    """
    amount = float(amount)
    summary = get_budget_summary()
    balance = summary["available_balance"]
    daily = get_daily_spend()
    reason = _check_spend_gates(balance, daily, amount)
    if reason:
        log.info("can_auto_spend BLOCK: %s", reason)
        return False
    return True


def try_auto_spend(
    amount: float,
    *,
    channel: str = "",
    experiment_id: str = "",
    note: str = "",
) -> dict:
    """
    Atomic check-and-spend. The production-safe primitive.

    Holds a write lock across the aggregate SELECTs and the INSERT so two
    concurrent callers cannot both pass the same gate check against the
    same budget (TOCTOU fix for :func:`can_auto_spend`).

    Returns ``{"spent": True,  "spend_id": int}`` on success,
            ``{"spent": False, "reason": str}`` if any gate blocks.
    """
    amount = float(amount)
    signed = -abs(round(amount, 2))
    target_day = _today()

    with db.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        # Collapse balance + daily-spend into a single scan.
        row = conn.execute(
            """SELECT
                 COALESCE(SUM(CASE WHEN type='allocation' THEN amount END), 0) AS allocated,
                 COALESCE(SUM(CASE WHEN type='spend'      THEN amount END), 0) AS spent_signed,
                 COALESCE(SUM(CASE WHEN type='spend' AND date=?
                                   THEN amount END), 0)                        AS daily_signed
               FROM growth_budget""",
            (target_day,),
        ).fetchone()
        allocated = round(float(row["allocated"]), 2)
        spent = round(abs(float(row["spent_signed"])), 2)
        daily = round(abs(float(row["daily_signed"])), 2)
        balance = round(allocated - spent, 2)

        reason = _check_spend_gates(balance, daily, amount)
        if reason:
            log.info("try_auto_spend BLOCK: %s", reason)
            # Exit the ``with`` block with no data change; commit is fine.
            return {"spent": False, "reason": reason}

        spend_id = _insert(
            conn,
            type_="spend",
            amount=signed,
            channel=channel,
            experiment_id=experiment_id,
            note=note,
            on_date=target_day,
        )

    log.info(
        "try_auto_spend OK: %.2f EUR (channel=%s exp=%s) → id=%d",
        signed, channel, experiment_id, spend_id,
    )
    return {"spent": True, "spend_id": spend_id}


def poll_stripe() -> list[dict]:
    """
    Poll Stripe for new succeeded charges and record them as donations.

    Degrades gracefully when:
      - STRIPE_API_KEY is not set       → returns []
      - `stripe` package is not installed → returns []

    Returns: list of dicts describing the newly-recorded donations.
    """
    if not getattr(config, "STRIPE_API_KEY", ""):
        log.warning("poll_stripe: STRIPE_API_KEY not set — skipping")
        return []

    try:
        import stripe  # type: ignore
    except ImportError:
        log.warning("poll_stripe: `stripe` package not installed — skipping")
        return []

    stripe.api_key = config.STRIPE_API_KEY

    new_donations: list[dict] = []
    try:
        charges = stripe.Charge.list(limit=20, status="succeeded")
        iterator = charges.auto_paging_iter() if hasattr(charges, "auto_paging_iter") else charges
        for charge in iterator:
            charge_id = getattr(charge, "id", None) or charge["id"]
            amount_cents = getattr(charge, "amount", None) or charge["amount"]
            amount_eur = round(float(amount_cents) / 100.0, 2)

            # Dedupe — already recorded?
            with db.get_conn() as conn:
                row = conn.execute(
                    """SELECT id FROM growth_budget
                       WHERE source=? AND type='donation' LIMIT 1""",
                    (charge_id,),
                ).fetchone()
            if row:
                continue

            result = record_donation(
                amount_eur,
                source=charge_id,
                note="stripe poll",
            )
            new_donations.append({
                "charge_id":         charge_id,
                "amount":            amount_eur,
                "donation_id":       result["donation_id"],
                "allocation_id":     result["allocation_id"],
                "allocated_amount":  result["allocated_amount"],
            })
    except Exception as e:  # noqa: BLE001 — Stripe may raise many error types
        log.error("poll_stripe: Stripe API error: %s", e)
        return new_donations  # whatever was already collected before the error

    if new_donations:
        log.info("poll_stripe: recorded %d new donation(s)", len(new_donations))
    return new_donations
