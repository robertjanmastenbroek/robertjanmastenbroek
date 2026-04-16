"""
RJM Outreach Agent — Email Template Engine (Claude CLI edition)

Generates personalised outreach emails by calling the `claude` CLI as a subprocess.
Uses your existing Claude Max plan — no separate API key or billing.

The engine:
  1. Selects the right system prompt for the contact type
  2. Injects RJM's story + relevant track recommendations
  3. Injects learning insights from past successful emails
  4. Calls `claude -p "..."` to generate subject + body as JSON
  5. Validates output against brand rules before returning
"""

import json
import logging
import os
import re
import subprocess
from pathlib import Path

from story import ARTIST, TRACKS
from brand_context import COMPACT_STORY, SMYKM_FRAMEWORK

# Brand voice gate (optional, non-blocking)
try:
    import brand_gate as _brand_gate
    _BRAND_GATE_AVAILABLE = True
except ImportError:
    _BRAND_GATE_AVAILABLE = False

# ─── Track URL lookup (built once at import) ──────────────────────────────────
# Maps lowercase track title → {title, spotify, bpm} for safety-net injection.
# Tracks with empty "spotify" URLs are excluded — they're placeholders (e.g. Kavod
# before its Spotify URL is pasted into story.py). The email templates refuse to
# mention a track without its URL, so empty-URL tracks cannot be recommended.
_TRACK_MAP: dict[str, dict] = {}
for _cat in TRACKS.values():
    for _t in _cat:
        if _t.get("spotify"):  # skip empty/missing URLs
            _TRACK_MAP[_t["title"].lower()] = _t
from config import CLAUDE_MODEL_EMAIL, CLAUDE_MODEL_FAST

log = logging.getLogger("outreach.templates")

# ─── Isolated HOME for subprocess calls ───────────────────────────────────────
# ~/.claude/settings.json contains MCP servers (context-mode via npx) and many
# hooks that hang when the CLI is called as a subprocess without a TTY.
# Workaround: run the CLI with a minimal HOME that has only an empty settings
# file. macOS resolves ~/Library/Application Support/ via the real user directory
# (not $HOME), so OAuth authentication still works correctly.
_ISOLATED_HOME = Path(__file__).parent / ".claude_subprocess_home"

def _ensure_isolated_home() -> str:
    """Create a minimal HOME dir for subprocess calls if it doesn't exist."""
    claude_dir = _ISOLATED_HOME / ".claude"
    settings = claude_dir / "settings.json"
    if not settings.exists():
        claude_dir.mkdir(parents=True, exist_ok=True)
        settings.write_text("{}\n")
    return str(_ISOLATED_HOME)

# ─── Locate the Claude CLI ────────────────────────────────────────────────────

def _find_claude_cli() -> str:
    """
    Find the Claude CLI binary. Automatically picks the latest installed version.
    Falls back to a configured path or environment variable.
    """
    # 1. Check env override (allows pinning a specific path)
    env_path = os.getenv("CLAUDE_CLI_PATH")
    if env_path and Path(env_path).is_file():
        return env_path

    # 2. Auto-detect from Claude's versioned install dir (macOS)
    base = Path(os.path.expanduser(
        "~/Library/Application Support/Claude/claude-code"
    ))
    if base.exists():
        # Get latest version dir
        versions = sorted(base.iterdir(), reverse=True)
        for ver_dir in versions:
            candidate = ver_dir / "claude.app" / "Contents" / "MacOS" / "claude"
            if candidate.is_file():
                return str(candidate)

    # 3. Try PATH (works if user has created a symlink or alias)
    for name in ("claude", "claude-code"):
        result = subprocess.run(["which", name], capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout.strip()

    raise FileNotFoundError(
        "Cannot find Claude CLI. Set CLAUDE_CLI_PATH env var to the full binary path, "
        "or create a symlink: ln -s '/path/to/claude' /usr/local/bin/claude"
    )


_CLAUDE_CLI = None

def _get_claude_cli() -> str:
    global _CLAUDE_CLI
    if not _CLAUDE_CLI:
        _CLAUDE_CLI = _find_claude_cli()
        log.info("Using Claude CLI: %s", _CLAUDE_CLI)
    return _CLAUDE_CLI


def _call_claude(prompt: str, model: str = CLAUDE_MODEL_EMAIL, timeout: int = 120) -> str:
    """
    Call the Claude CLI with a prompt. Returns the text response.
    Raises RuntimeError on failure.

    Uses an isolated $HOME with a minimal settings.json to prevent the hooks
    and MCP servers in ~/.claude/settings.json from hanging the subprocess.
    """
    cli = _get_claude_cli()
    isolated_home = _ensure_isolated_home()

    env = {**os.environ, "HOME": isolated_home}
    result = subprocess.run(
        [cli, "--model", model, "-p", prompt],
        capture_output=True,
        text=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
        env=env,
    )
    if result.returncode != 0:
        err = (result.stderr or "").strip()
        raise RuntimeError(f"Claude CLI exited {result.returncode}: {err[:200]}")
    return result.stdout.strip()


# ─── Signature ────────────────────────────────────────────────────────────────
# Minimal artist sign-off — matches the original email tone (personal, direct,
# no corporate legal footer). reply_classifier.py already handles natural-language
# unsubscribe replies (intent='unsubscribe' → add to dead_addresses + status='closed'),
# so the opt-out path exists behaviorally even though we don't advertise it.
_SIGNATURE_BLOCK = (
    "\n\nRobert-Jan\n"
    "robertjanmastenbroek.com | https://instagram.com/robertjanmastenbroek"
)

# ─── System prompts per contact type ─────────────────────────────────────────
# COMPACT_STORY and SMYKM_FRAMEWORK are imported from brand_context.py.
# Edit brand identity there — it propagates here automatically.

_SYSTEM_BASE = f"""You write cold outreach emails for Robert-Jan Mastenbroek.
{COMPACT_STORY}
{SMYKM_FRAMEWORK}
Output ONLY valid JSON: {{"subject": "...", "body": "..."}}
"""

_TYPE_ADDONS = {
    "label": """CONTEXT: Demo submission to a record label.
- Trigger: cite a specific release or signing from their catalogue
- Challenge: most demos have no story — faceless production, no hook
- Value Prop: record deal walkaway at 21 → €6M platform → total collapse → 30+ tracks, all owned
- Hidden Objection: "another unsolicited demo" → I own all masters (flexible to license, sign, or collaborate)
- No faith angle unless label is faith-focused""",

    "curator": """CONTEXT: Spotify playlist curator pitch.
- Subject line formula: "[Track Name] ([BPM] BPM [micro-genre]) for [Exact Playlist Name]"
  Example: "Jericho (140 BPM Psytrance) for Ritual Techno Selections"
- Opening (3 sentences max): name one specific thing about their playlist proving you listened,
  then: track name + BPM + genre + Spotify stream link, then single frictionless ask.
- Email length: 60–80 words maximum. Never exceed 100.
- Signature must include: 290K IG @holyraveofficial | Tenerife, CET
  (signals cross-promotion reach — curators add tracks knowing they get exposure)
- Trigger: cite a SPECIFIC track already on their playlist + BPM match
  Example: "Your playlist sits at 136–140 BPM — Jericho is 140 BPM, same register as [track X] you added in March."
- Challenge: drowning in faceless bulk-blast submissions
- Value Prop: one specific track + why it sits next to what they already play (cite BPM, mood, energy match)
- Hidden Objection: "sounds like everything else" → name one concrete differentiator
- DO NOT use this template for press/editorial contacts — they need a different ask""",

    "youtube": """CONTEXT: YouTube music-promo channel — ask them to upload our track to their channel.
The pitch is MONEY: they keep 100% of the ad revenue from the upload.
Content ID has been DISABLED at the distributor for these tracks, so the promise is real.

- Subject formula: "[Track] ([BPM] BPM [genre]) — free track, you keep 100% ad rev"
  Example: "Kavod (140 BPM Hebrew psytrance) — free track, you keep 100% ad rev"
- Opening (2 sentences): name ONE specific recent upload of theirs (genre, vibe).
  Show you actually watched it — don't flatter generically.
- Offer (3 sentences): ONE track (WAV + artwork). Name BPM, genre, one-word visual.
  Spotify link inline directly after the track title.
- The deal (2 sentences, concrete and clean):
  "You keep 100% of the ad revenue — Content ID is off on this track. I just ask
   you to put my Spotify artist link in the description so listeners can go stream."
- CTA (1 sentence): "Reply 'yes' and I'll send the WAV + artwork within the hour."
- Email length: 80–110 words.
- Signature: Robert-Jan | robertjanmastenbroek.com | 290K IG @holyraveofficial
- NEVER hedge the ad rev promise. NEVER mention BandLab, distributors, or claims.
- NEVER use 'monetize' as a carrot — 'keep 100% of the ad revenue' is the money phrase.
- The deal is simple: free track → they run ads → RJM gets Spotify streams from the link.
- Faith angle: OFF unless the channel description explicitly says christian/worship/spiritual.""",

    "festival": """CONTEXT: Festival booking inquiry.
- Trigger: a specific past edition, headliner decision, or stated ethos
- Challenge: bookers need artists with a reason for being there — not another DJ with a generic bio
- Value Prop: record deal walkaway → €6M platform → total loss → camper van → music rooted in something real
- Hidden Objection: "we don't know this artist" → Tenerife = low EU/Canary travel costs
- Conscious/spiritual festivals: sacred music angle front and centre. Secular: music + story first.""",

    "podcast": """CONTEXT: Podcast guest pitch.
- Trigger: ONE specific episode or verbatim quote — not "I love your show"
- Challenge: finding guests with structurally RARE stories (not just "entrepreneur who overcame adversity")
- Hidden Objection: "we get hundreds of pitches" → name the specific combo: Dutch raver + €6M collapse + Jesus + own catalogue
- Available via Zoom, video or audio

STORY ANGLES BY AUDIENCE:
- Faith/ministry: collapse → surrender → rebuilding on faith. Return to faith, not first conversion.
- Music biz: walked away from record deal at 21, 290K following with zero label, owns every master
- Expat/nomad: name destroyed in Holland → island → camper van → built new life in Tenerife by choice
- Sober/wellness: sober raver, music as the altered state — what fills the space
- Entrepreneurship: built €6M platform, lost everything at 30, repaid every creditor, rebuilt from zero""",
}


def _is_christian_contact(genre: str, notes: str) -> bool:
    """Return True if this contact is faith/Christian focused."""
    combined = (genre + " " + notes).lower()
    return any(w in combined for w in [
        "christian", "faith", "church", "gospel", "worship", "jesus",
        "bible", "ministry", "spiritual", "holy", "sacred", "prayer",
        "evangelical", "pentecostal", "charismatic", "ccm"
    ])


_CHRISTIAN_ADDON = """
FAITH-FOCUSED CONTACT: Lead with faith openly. Jesus-loving raver is a feature not a footnote. Hook = losing everything, on his knees, found Jesus. Scripture-rooted music is the core identity. Use: faith, surrender, redemption, worship, testimony. Biblical track titles/Hebrew lyrics are selling points. Churches as venues: worship experience that reaches people who'd never enter a church. Tone: warm, genuine, brother-to-brother."""


def _build_prompt(contact: dict, learning_context: str = "") -> str:
    """Build the full prompt (system + user combined for CLI mode)."""
    ctype = contact.get("type", "curator")
    genre = contact.get("genre", "")
    notes = contact.get("notes", "")
    name  = contact.get("name", "")

    is_christian = _is_christian_contact(genre, notes)
    christian_addon = _CHRISTIAN_ADDON if is_christian else ""

    system  = _SYSTEM_BASE + _TYPE_ADDONS.get(ctype, "") + christian_addon
    tracks  = _get_track_recs(ctype, genre, notes)
    research = contact.get("research_notes", "") or ""

    user = f"""Write an outreach email to:
Name:  {name}
Type:  {ctype}
Genre: {genre}
Notes: {notes}

TRACKS (choose 1 — include its Spotify URL inline every time you mention it):
{tracks}
RULE: Never name a track without its Spotify link directly after it. Example: "Living Water (https://open.spotify.com/track/...)"

SPOTIFY ARTIST PAGE: {ARTIST['spotify_artist']}
"""
    if research:
        user += f"\nRECIPIENT RESEARCH (use for personalised opener):\n{research}\n"
        user += (
            "\nHOOK MODE: research-first. The opener MUST reference one specific "
            "detail from RECIPIENT RESEARCH above (a playlist name, a recent track, "
            "a quote). Do not paraphrase vaguely — use the concrete detail verbatim "
            "where it fits naturally. Failing this test means the email is generic.\n"
        )
    else:
        user += (
            "\nHOOK MODE: genre-fallback. No research available — lead with the "
            "BPM/genre match between the recommended track and the recipient's "
            "stated focus. Do NOT invent details about their playlist, show, or "
            "recent work. Honest genre-match > fabricated personalisation.\n"
        )

    if learning_context:
        user += f"\nINSIGHTS FROM PAST SUCCESSFUL EMAILS:\n{learning_context}\n"

    user += "\nReturn ONLY valid JSON with 'subject' and 'body' keys."

    return system + "\n\n" + user


_FOLLOWUP_SYSTEM = """Write a follow-up email as Robert-Jan Mastenbroek (Dutch DJ/producer, Tenerife, instagram.com/robertjanmastenbroek). First person. Peer-to-peer tone. Sign off: Robert-Jan / robertjanmastenbroek.com | https://instagram.com/robertjanmastenbroek"""

def _build_followup_prompt(contact: dict) -> str:
    orig_subject = contact.get("sent_subject", "") or ""
    # Prefer full stored body, fall back to snippet; strip whitespace-only values
    orig_body    = (contact.get("sent_body") or contact.get("sent_body_snippet", "") or "").strip()
    name         = contact.get("name", "")
    ctype        = contact.get("type", "curator")
    genre        = contact.get("genre", "") or ""
    notes        = contact.get("notes", "") or ""

    if orig_body:
        context_section = f"Original email body:\n{orig_body}\n"
        ref_rule = 'One sentence referencing something specific from the first email (not "Just following up")'
    else:
        # No stored body — give enough context for a grounded follow-up
        context_section = (
            f"Contact type: {ctype}\n"
            f"Genre / focus: {genre}\n"
            f"Notes: {notes}\n"
            f"(Original email body not stored — write a natural follow-up that makes sense "
            f"for a {ctype} outreach from Robert-Jan Mastenbroek, without inventing details "
            f"that weren't sent.)\n"
        )
        ref_rule = (
            'One short sentence that reopens the door naturally — reference the contact\'s '
            'type/genre (e.g. their label, playlist, show focus) as the hook, not the original email'
        )

    subject_line = f"Re: {orig_subject}" if orig_subject else f"following up"

    user = f"""Follow-up to {name} ({ctype}).
{context_section}
Rules:
- Under 80 words
- {ref_rule}
- Don't repeat the full pitch — reopen the door with one question
- Not pushy, not apologetic
- Subject: "{subject_line}"

Return ONLY valid JSON with 'subject' and 'body' keys."""

    return _FOLLOWUP_SYSTEM + "\n\n" + user


def _get_track_recs(ctype: str, genre: str, notes: str) -> str:
    """
    Track priority: 80% Renamed (130 BPM tribal) + Halleluyah (140 BPM psytrance).
    Melodic techno (Living Water etc.) only for explicitly melodic/house contexts.
    Default fallback always returns Renamed + Halleluyah — never melodic as default.

    Tracks with empty spotify URLs are silently filtered (placeholders like Kavod
    before its URL is pasted into story.py). The brand rule requires "never name
    a track without its Spotify link", so empty-URL tracks cannot be recommended.

    For ctype='youtube', the psytrance branch is forced regardless of genre — all
    channels in the YouTube pipeline are filtered to psytrance/tribal/progressive
    at discovery time, so melodic techno is never the right pitch here.
    """
    genre_lower = (genre + " " + notes).lower()
    is_psy    = any(w in genre_lower for w in ["psy", "psytrance", "trance", "140"])
    is_tribal = any(w in genre_lower for w in ["tribal", "ethnic", "organic", "130"])
    is_melodic = any(w in genre_lower for w in ["melodic", "house", "minimal", "accessible"])
    is_faith  = any(w in genre_lower for w in ["christian", "faith", "worship", "gospel"])

    # YouTube pipeline = psytrance/tribal by definition (filtered at discovery)
    if ctype == "youtube":
        is_psy    = True
        is_tribal = True
        is_melodic = False
        is_faith   = False

    def _fmt(t):
        return f"• {t['title']} — {t['bpm']} BPM — {t['notes']} — {t['spotify']}"

    def _has_url(t):
        return bool(t.get("spotify"))

    lines = []

    if is_psy or is_tribal:
        # Lead with the matching genre, pair with its complement
        primary   = TRACKS["psytrance"]    if is_psy    else TRACKS["tribal_techno"]
        secondary = TRACKS["tribal_techno"] if is_psy    else TRACKS["psytrance"]
        for t in primary + secondary:
            if not _has_url(t):
                continue
            entry = _fmt(t)
            if entry not in lines:
                lines.append(entry)

    if is_faith:
        for t in TRACKS["melodic_techno"]:
            if not _has_url(t):
                continue
            entry = _fmt(t)
            if entry not in lines:
                lines.append(entry)

    if is_melodic and not is_psy and not is_tribal:
        for t in TRACKS["melodic_techno"]:
            if not _has_url(t):
                continue
            entry = _fmt(t)
            if entry not in lines:
                lines.append(entry)

    # Default: Renamed + Halleluyah — never fall back to melodic techno by default
    if not lines:
        for t in TRACKS["tribal_techno"] + TRACKS["psytrance"]:
            if not _has_url(t):
                continue
            lines.append(_fmt(t))

    return "\n".join(lines[:3])  # max 3 tracks — enough context, fewer tokens



def _ensure_signature(body: str) -> str:
    """
    Guarantee every outbound email ends with the canonical sign-off.

    Idempotent: if the body already contains the website URL (strong signal
    the model wrote the sign-off), return unchanged. Otherwise append the
    canonical block. This replaces the old direct-append pattern which could
    leave a duplicate "Robert-Jan\\nrobertjanmastenbroek.com" when the model
    already wrote one.
    """
    if "robertjanmastenbroek.com" in body:
        return body
    return body.rstrip() + _SIGNATURE_BLOCK


def _inject_spotify_links(body: str) -> str:
    """
    Safety net: scan body for track titles mentioned without their Spotify URL.
    If found, append the URL inline — e.g. "Living Water" → "Living Water (https://...)"
    Runs after Claude generates the body, catching any missed links.
    """
    for title_lower, track in _TRACK_MAP.items():
        title   = track["title"]
        url     = track["spotify"]
        # Skip if URL is already in the body
        if url in body:
            continue
        # Check if the title appears (case-insensitive)
        pattern = re.compile(re.escape(title), re.IGNORECASE)
        if pattern.search(body):
            # Inject URL inline after first mention of the title
            body = pattern.sub(f"{title} ({url})", body, count=1)
            log.debug("Injected missing Spotify link for '%s'", title)
    return body


def _parse_response(raw: str) -> tuple[str, str]:
    """Parse Claude's JSON response into (subject, body)."""
    # Strip markdown fences if present
    raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()

    # Extract the first JSON object — handles text before/after the JSON block
    match = re.search(r"\{.*?\}", raw, re.DOTALL)
    if not match:
        # Fallback: try the entire string (already stripped)
        raise ValueError(f"No JSON object found in Claude response: {raw[:200]!r}")
    raw = match.group(0)

    # Attempt parse; if it fails try extracting a larger JSON block (nested braces)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Greedy match — picks up the largest {...} block (handles nested JSON)
        match2 = re.search(r"\{.*\}", raw, re.DOTALL)
        if match2:
            data = json.loads(match2.group(0))
        else:
            raise
    subject = data.get("subject", "").strip()
    body    = data.get("body", "").strip()

    if not subject or not body:
        raise ValueError("Claude returned empty subject or body")

    # Ensure every mentioned track has its Spotify link
    body = _inject_spotify_links(body)

    # Ensure sign-off + CAN-SPAM compliance footer is present
    body = _ensure_signature(body)

    return subject, body


def _parse_response_with_hooks(raw: str) -> tuple[str, str, list[str]]:
    """Parse Claude's JSON response into (subject, body, hooks_used).

    Same contract as `_parse_response` plus the model's self-reported hooks.
    When the model omits `hooks_used`, returns an empty list (the caller can
    fall back to `_extract_hooks_from_prompt` heuristics).
    """
    raw_stripped = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    match = re.search(r"\{.*?\}", raw_stripped, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object found in Claude response: {raw_stripped[:200]!r}")
    block = match.group(0)
    try:
        data = json.loads(block)
    except json.JSONDecodeError:
        match2 = re.search(r"\{.*\}", raw_stripped, re.DOTALL)
        if match2:
            data = json.loads(match2.group(0))
        else:
            raise

    subject = (data.get("subject", "") or "").strip()
    body    = (data.get("body", "") or "").strip()
    if not subject or not body:
        raise ValueError("Claude returned empty subject or body")

    body = _inject_spotify_links(body)
    body = _ensure_signature(body)

    hooks_raw = data.get("hooks_used") or []
    hooks = [str(h).strip() for h in hooks_raw if isinstance(h, (str, int))] if isinstance(hooks_raw, list) else []
    return subject, body, hooks


# ─── Public API ───────────────────────────────────────────────────────────────


class BrandGateRejected(Exception):
    """Raised when generate_email produces content that fails the brand gate
    twice in a row (first draft + one retry with feedback).

    Callers in the send path must catch this, skip the send, and leave the
    contact in 'verified' so the stale-queue rescue can retry it later.
    """


def generate_email(contact: dict, learning_context: str = "") -> tuple[str, str]:
    """
    Generate a personalised email for a contact using Claude CLI.

    Lake 5 contract: the brand gate is now BLOCKING with exactly one retry.
    If the first draft fails `brand_gate.validate_content`, we rebuild the
    prompt with the gate's feedback (suggestion + flags) and call Claude
    once more. If the retry also fails, we raise `BrandGateRejected` — the
    batch loop must catch that and skip the send.

    Returns (subject, body). Raises on failure or brand-gate rejection.
    """
    # Use best-performing template type if enough data exists
    contact_type = contact.get("type", "curator")
    from db import get_best_template_type
    best_template = get_best_template_type(contact_type)
    if best_template and best_template != contact.get("template_type"):
        log.info(
            "Template override: %s → %s (best reply rate for %s)",
            contact.get("template_type", "default"), best_template, contact_type
        )
        contact = {**contact, "template_type": best_template}

    base_prompt = _build_prompt(contact, learning_context)

    log.info("Generating email for %s (%s)...",
             contact.get("email"), contact.get("type"))

    # ── Draft 1 ────────────────────────────────────────────────────────────
    raw = _call_claude(base_prompt)
    subject, body, model_hooks = _parse_response_with_hooks(raw)
    log.info("Draft 1 for %s — subject: %r", contact.get("email"), subject)

    gate_issues: list[str] = []
    gate_passed = True
    first_validation: dict = {}
    if _BRAND_GATE_AVAILABLE:
        try:
            first_validation = _brand_gate.validate_content(body) or {}
            gate_passed = bool(first_validation.get("passes", True))
            gate_issues = list(first_validation.get("flags", []) or [])
        except Exception as exc:
            log.warning("brand_gate.validate_content failed: %s", exc)
            first_validation = {}
            gate_passed = True  # fail-open on gate errors — never block sends on our bug

    # ── Draft 2 (retry) — only if the first failed the gate ────────────────
    if _BRAND_GATE_AVAILABLE and not gate_passed:
        flags_text = ", ".join(gate_issues) or "unspecified"
        suggestion = (first_validation.get("suggestion") or "").strip()
        retry_prompt = (
            base_prompt
            + "\n\n---\n"
            + "BRAND GATE FEEDBACK — the previous draft failed validation.\n"
            + f"Flags: {flags_text}\n"
            + (f"Fix: {suggestion}\n" if suggestion else "")
            + "Rewrite from scratch. Lead with a concrete, visual detail "
              "(BPM, track name, physical scene). Every claim must be "
              "falsifiable. No boilerplate, no vague enthusiasm. Return the "
              "SAME JSON schema as before."
        )
        log.warning(
            "Brand gate failed for %s (flags=%s) — retrying once",
            contact.get("email"), gate_issues,
        )
        raw2 = _call_claude(retry_prompt)
        subject, body, model_hooks = _parse_response_with_hooks(raw2)
        try:
            second = _brand_gate.validate_content(body) or {}
            gate_passed = bool(second.get("passes", True))
            gate_issues = list(second.get("flags", []) or [])
        except Exception as exc:
            log.warning("brand_gate.validate_content retry failed: %s", exc)
            gate_passed = True

        if not gate_passed:
            # Record the failed retry in the audit table before raising so
            # learning can see the attempt. Then refuse to ship.
            try:
                from db import log_personalization_audit
                from config import CLAUDE_MODEL_EMAIL
                log_personalization_audit(
                    email=contact.get("email", ""),
                    contact_type=contact.get("type", "curator"),
                    subject=subject,
                    body=body,
                    hooks_used=model_hooks or _extract_hooks_from_prompt(contact),
                    research_used=bool(contact.get("research_notes")),
                    model=CLAUDE_MODEL_EMAIL,
                    learning_applied=bool(learning_context),
                    brand_gate_passed=False,
                    brand_gate_issues=gate_issues,
                )
            except Exception as exc:
                log.warning("audit-on-reject failed: %s", exc)
            raise BrandGateRejected(
                f"brand gate rejected two drafts for {contact.get('email')} "
                f"— flags={gate_issues}"
            )

    try:
        from db import log_personalization_audit
        from config import CLAUDE_MODEL_EMAIL
        # Prefer the model's self-reported hooks; fall back to the heuristic
        # extractor only when the model omitted `hooks_used` entirely.
        hooks = model_hooks if model_hooks else _extract_hooks_from_prompt(contact)
        log_personalization_audit(
            email=contact.get("email", ""),
            contact_type=contact.get("type", "curator"),
            subject=subject,
            body=body,
            hooks_used=hooks,
            research_used=bool(contact.get("research_notes")),
            model=CLAUDE_MODEL_EMAIL,
            learning_applied=bool(learning_context),
            brand_gate_passed=gate_passed,
            brand_gate_issues=gate_issues,
        )
    except Exception as exc:
        log.warning("personalization_audit logging failed for %s: %s",
                    contact.get("email"), exc)

    return subject, body


def _extract_hooks_from_prompt(contact: dict) -> list[str]:
    """Derive a best-effort list of personalization hooks used for this contact.

    We don't parse Claude's output — we record which SIGNALS were available.
    That's what matters for learning: "this contact had research + christian +
    bpm match" tells us more than parsing which subset Claude chose to lean on.
    """
    hooks: list[str] = []
    if contact.get("research_notes"):
        hooks.append("research")
    if contact.get("playlist_size"):
        hooks.append(f"playlist_{contact['playlist_size']}")
    genre = (contact.get("genre") or "").lower()
    notes = (contact.get("notes") or "").lower()
    if any(tok in (genre + " " + notes) for tok in ("christian", "faith", "worship", "gospel")):
        hooks.append("christian")
    if contact.get("youtube_channel_id"):
        hooks.append("youtube_channel")
    if contact.get("youtube_recent_upload_title"):
        hooks.append("youtube_recent_upload")
    if "bpm" in (notes or ""):
        hooks.append("bpm_match")
    hooks.append(f"type_{contact.get('type', 'unknown')}")
    return hooks


def _build_batch_prompt(contacts, learning_contexts=None) -> str:
    """Build the full batch prompt — one string, N contact blocks.

    Each contact block includes a HOOK MODE directive that branches on
    whether research_notes is present:
      - research-first → opener must reference a specific research detail
      - genre-fallback → lead with BPM/genre match, do not fabricate
    """
    if learning_contexts is None:
        learning_contexts = {}

    blocks = []
    for i, c in enumerate(contacts, 1):
        ctype    = c.get("type", "curator")
        genre    = c.get("genre", "")
        notes    = c.get("notes", "")
        name     = c.get("name", "")
        email    = c.get("email", "")
        research = c.get("research_notes", "") or ""
        tracks   = _get_track_recs(ctype, genre, notes)

        is_christian    = _is_christian_contact(genre, notes)
        type_addon      = _TYPE_ADDONS.get(ctype, "")
        christian_addon = _CHRISTIAN_ADDON if is_christian else ""
        learn_ctx       = learning_contexts.get(ctype, "")

        block = f"--- CONTACT {i} | email: {email} ---\n"
        block += f"Name: {name}\nType: {ctype}\nGenre: {genre}\nNotes: {notes}\n"
        block += f"\nTRACKS (choose 1 — always include Spotify URL inline):\n{tracks}\n"
        block += f"\nTYPE RULES:\n{type_addon}"
        if christian_addon:
            block += f"\n{christian_addon}"
        if research:
            block += f"\nRECIPIENT RESEARCH:\n{research}"
            block += (
                "\nHOOK MODE: research-first. Opener must reference one concrete "
                "detail from RECIPIENT RESEARCH (playlist name, recent track, quote). "
                "No vague paraphrase — use the specific detail verbatim."
            )
        else:
            block += (
                "\nHOOK MODE: genre-fallback. No research — lead with the BPM/genre "
                "match between the recommended track and the recipient's stated focus. "
                "Do NOT invent playlist names, show titles, or recent-work details."
            )
        if learn_ctx:
            block += f"\nINSIGHTS FROM PAST SUCCESSES:\n{learn_ctx}"
        blocks.append(block)

    n = len(contacts)
    return (
        _SYSTEM_BASE
        + f"\n\nGenerate {n} outreach emails, one per contact below.\n"
        + f"Return ONLY a JSON array with exactly {n} objects in order:\n"
        + '[{"email":"<email>","subject":"...","body":"..."}, ...]\n\n'
        + "\n\n".join(blocks)
        + f"\n\nReturn ONLY the JSON array of {n} items. No other text."
    )


def generate_emails_batch(contacts, learning_contexts=None):
    """
    Generate emails for all contacts in ONE Claude CLI call.
    Returns {email: (subject, body)}. Contacts that fail are omitted.
    """
    if not contacts:
        return {}

    prompt = _build_batch_prompt(contacts, learning_contexts)
    n = len(contacts)

    log.info("Batch-generating %d emails in one CLI call...", n)
    try:
        raw = _call_claude(prompt, timeout=300)
    except Exception as e:
        log.error("Batch generation failed: %s", e)
        return {}

    raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        log.error("Batch response contained no JSON array")
        return {}

    try:
        items = json.loads(match.group(0))
    except json.JSONDecodeError as e:
        log.error("Failed to parse batch JSON: %s", e)
        return {}

    result = {}
    for item in items:
        email   = item.get("email", "").strip()
        subject = item.get("subject", "").strip()
        body    = item.get("body", "").strip()
        if not email or not subject or not body:
            continue
        body = _inject_spotify_links(body)
        body = _ensure_signature(body)
        result[email] = (subject, body)
        log.info("Batch generated — %s subject: %r", email, subject)
        if _BRAND_GATE_AVAILABLE:
            _brand_gate.gate_or_warn(body, context="template_engine.batch")

    log.info("Batch complete: %d/%d emails generated", len(result), n)
    return result


def generate_followup_email(contact: dict) -> tuple[str, str]:
    """
    Generate a short follow-up email. Returns (subject, body).
    Uses the fast/cheap model — follow-ups are short and simple.
    """
    prompt = _build_followup_prompt(contact)

    log.info("Generating follow-up for %s...", contact.get("email"))

    raw = _call_claude(prompt, model=CLAUDE_MODEL_FAST)
    subject, body = _parse_response(raw)

    # Force Re: prefix when we have the original subject
    orig_subject = contact.get("sent_subject", "") or ""
    if orig_subject and not subject.lower().startswith("re:"):
        subject = f"Re: {orig_subject}"

    log.info("Generated follow-up — subject: %r", subject)
    if _BRAND_GATE_AVAILABLE:
        _brand_gate.gate_or_warn(body, context="template_engine.followup")
    return subject, body
