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


def can_auto_spend(amount: float) -> bool:
    """
    Auto-spend gate. Returns True only if ALL caps hold:

      1. amount ≤ BTL_AUTO_SPEND_MAX_EUR
      2. balance − amount ≥ BTL_RESERVE_MIN_EUR
      3. daily_spend + amount ≤ min(BTL_DAILY_SPEND_CAP_EUR,
                                    balance × BTL_DAILY_SPEND_CAP_PCT)
    """
    amount = float(amount)
    if amount <= 0:
        return False

    # Gate 1 — per-action ceiling
    if amount > config.BTL_AUTO_SPEND_MAX_EUR:
        log.info("can_auto_spend BLOCK: amount %.2f > per-action max %.2f",
                 amount, config.BTL_AUTO_SPEND_MAX_EUR)
        return False

    summary = get_budget_summary()
    balance = summary["available_balance"]

    # Gate 2 — reserve floor
    if balance - amount < config.BTL_RESERVE_MIN_EUR:
        log.info("can_auto_spend BLOCK: balance %.2f − %.2f < reserve %.2f",
                 balance, amount, config.BTL_RESERVE_MIN_EUR)
        return False

    # Gate 3 — daily cap (lesser of EUR cap and pct-of-balance cap)
    daily = get_daily_spend()
    pct_cap = balance * config.BTL_DAILY_SPEND_CAP_PCT
    daily_cap = min(config.BTL_DAILY_SPEND_CAP_EUR, pct_cap)
    if daily + amount > daily_cap + 1e-9:  # tiny epsilon for FP equality
        log.info(
            "can_auto_spend BLOCK: daily %.2f + %.2f > cap %.2f (eur=%.2f, pct=%.2f)",
            daily, amount, daily_cap, config.BTL_DAILY_SPEND_CAP_EUR, pct_cap,
        )
        return False

    return True


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
