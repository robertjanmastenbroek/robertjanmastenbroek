"""
reclassify_contacts.py — One-time migration to relationship-first contact model.

Maps existing contacts.type + contacts.status → contacts.persona + contacts.relationship_stage
+ contacts.warmth_score + contacts.outreach_goal.

Safe to re-run — only updates contacts where persona IS NULL (i.e. not yet classified).

Usage:
  python3 reclassify_contacts.py           # classify all unclassified contacts
  python3 reclassify_contacts.py --all     # re-classify everything (force)
  python3 reclassify_contacts.py --dry-run # preview without writing
"""

from __future__ import annotations

import argparse
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
import db

log = logging.getLogger("outreach.reclassify")

# ── Type → Persona mapping ────────────────────────────────────────────────────

TYPE_TO_PERSONA: dict[str, str] = {
    "curator":       "curator",
    "podcast":       "podcast",
    "youtube":       "genre_creator",
    "festival":      "event_promoter",
    "booking_agent": "event_promoter",
    "wellness":      "retreat",
    "blog":          "lifestyle_creator",
    "community":     "community_leader",
    "label":         "curator",
    "sync":          "curator",
    # New personas (pass-through)
    "faith_creator":      "faith_creator",
    "church":             "church",
    "retreat":            "retreat",
    "ecstatic_dance":     "ecstatic_dance",
    "rave_photographer":  "rave_photographer",
    "sound_engineer":     "sound_engineer",
    "conscious_promoter": "conscious_promoter",
    "lifestyle_creator":  "lifestyle_creator",
    "digital_nomad":      "digital_nomad",
    "surfer":             "surfer",
    "sacred_artist":      "sacred_artist",
    "genre_creator":      "genre_creator",
    "community_leader":   "community_leader",
    "event_promoter":     "event_promoter",
    "micro_influencer":   "lifestyle_creator",
}

# ── Status → Relationship stage + warmth ─────────────────────────────────────

STATUS_TO_STAGE: dict[str, tuple[str, int]] = {
    "new":            ("discovered",   5),
    "verified":       ("discovered",   5),
    "queued":         ("discovered",  10),
    "sent":           ("first_touch",  20),
    "followup_sent":  ("first_touch",  30),
    "responded":      ("responded",    60),
    "won":            ("collaborating",90),
    "warm_up":        ("discovered",   5),
    "bounced":        ("discovered",   0),
    "skip":           ("discovered",   0),
    "dead_letter":    ("discovered",   0),
    "invalid":        ("discovered",   0),
}

# ── Persona → default outreach goal ──────────────────────────────────────────

PERSONA_GOALS: dict[str, str] = {
    "faith_creator":      "relationship",
    "church":             "booking",
    "retreat":            "booking",
    "ecstatic_dance":     "booking",
    "rave_photographer":  "collaboration",
    "sound_engineer":     "relationship",
    "conscious_promoter": "booking",
    "lifestyle_creator":  "relationship",
    "digital_nomad":      "relationship",
    "surfer":             "relationship",
    "sacred_artist":      "collaboration",
    "genre_creator":      "music_share",
    "curator":            "music_share",
    "podcast":            "music_share",
    "event_promoter":     "booking",
    "community_leader":   "relationship",
}


def classify_one(contact: dict) -> dict:
    """Return updated fields for a single contact row."""
    ctype = (contact.get("type") or "curator").lower().strip()
    status = (contact.get("status") or "new").lower().strip()
    notes = (contact.get("notes") or "").lower()
    genre = (contact.get("genre") or "").lower()

    # Persona
    persona = TYPE_TO_PERSONA.get(ctype, "curator")

    # Upgrade persona based on notes/genre signals
    if "faith" in notes or "christian" in notes or "church" in notes:
        if persona not in ("ecstatic_dance", "church", "retreat"):
            persona = "faith_creator"
    if "ecstatic dance" in notes or "ecstatic_dance" in ctype:
        persona = "ecstatic_dance"
    if "photographer" in notes or "videographer" in notes:
        persona = "rave_photographer"
    if "sound engineer" in notes or "sound tech" in notes:
        persona = "sound_engineer"
    if "nomad" in notes or "digital nomad" in notes:
        persona = "digital_nomad"
    if "surf" in notes:
        persona = "surfer"

    # Relationship stage + warmth
    stage, warmth = STATUS_TO_STAGE.get(status, ("discovered", 5))

    # Outreach goal
    goal = PERSONA_GOALS.get(persona, "relationship")

    # Faith signals
    faith_signals = 0
    faith_keywords = ["christian", "faith", "jesus", "church", "worship", "holy", "biblical", "spiritual"]
    pagan_keywords = ["pagan", "wicca", "occult", "ayahuasca", "plant medicine ceremony"]

    if any(k in notes for k in faith_keywords) or any(k in genre for k in faith_keywords):
        faith_signals = 2
    elif "spiritual" in notes or "conscious" in notes or "ecstatic" in notes:
        faith_signals = 1
    if any(k in notes for k in pagan_keywords):
        faith_signals = -1  # flag for review

    return {
        "persona":             persona,
        "relationship_stage":  stage,
        "warmth_score":        warmth,
        "outreach_goal":       goal,
        "faith_signals":       faith_signals,
    }


def run(force: bool = False, dry_run: bool = False) -> dict:
    """Classify unclassified contacts (or all, if force=True)."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

    with db.get_conn() as conn:
        if force:
            rows = conn.execute("SELECT id, type, status, notes, genre FROM contacts").fetchall()
        else:
            rows = conn.execute(
                "SELECT id, type, status, notes, genre FROM contacts WHERE persona IS NULL"
            ).fetchall()

        log.info("Contacts to classify: %d", len(rows))

        updated = 0
        by_persona: dict[str, int] = {}
        by_stage: dict[str, int] = {}

        for row in rows:
            contact = {
                "id": row[0], "type": row[1], "status": row[2],
                "notes": row[3], "genre": row[4],
            }
            fields = classify_one(contact)

            by_persona[fields["persona"]] = by_persona.get(fields["persona"], 0) + 1
            by_stage[fields["relationship_stage"]] = by_stage.get(fields["relationship_stage"], 0) + 1

            if not dry_run:
                conn.execute(
                    """UPDATE contacts SET
                       persona = ?, relationship_stage = ?, warmth_score = ?,
                       outreach_goal = ?, faith_signals = ?
                       WHERE id = ?""",
                    (
                        fields["persona"], fields["relationship_stage"],
                        fields["warmth_score"], fields["outreach_goal"],
                        fields["faith_signals"], contact["id"],
                    ),
                )
            updated += 1

    log.info("Classified %d contacts", updated)
    print(f"\n{'DRY-RUN ' if dry_run else ''}Reclassification complete: {updated} contacts")
    print("\nBy persona:")
    for persona, count in sorted(by_persona.items(), key=lambda x: -x[1]):
        print(f"  {persona:<22} {count:>4}")
    print("\nBy relationship stage:")
    for stage, count in sorted(by_stage.items(), key=lambda x: -x[1]):
        print(f"  {stage:<22} {count:>4}")

    return {"updated": updated, "by_persona": by_persona, "by_stage": by_stage}


def main() -> None:
    parser = argparse.ArgumentParser(description="Reclassify contacts to relationship model")
    parser.add_argument("--all", dest="force", action="store_true",
                        help="Re-classify all contacts (not just unclassified)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    args = parser.parse_args()
    run(force=args.force, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
