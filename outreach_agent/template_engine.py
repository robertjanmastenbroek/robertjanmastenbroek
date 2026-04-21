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

    "curator": """
═══ CURATOR EMAIL ═══

THE FRAME:
A curator's fear is not "this track is bad" — it is "I missed something good." Every sentence should quietly reinforce one of two self-images: "I have good ears — this person noticed what I noticed" OR "This artist is moving — I found them early." You're not petitioning a gatekeeper. You're one selector writing to another.

SUBJECT LINE — not a filing system, a hook:

Use ONE of these formulas:
  Contrarian Fact: "[Track name]: [one unexpected true thing]"
    GOOD: "Jericho: psytrance built around a 3,000-year-old battle cry"
    GOOD: "Renamed: tribal techno that ends in 8 seconds of silence"
  Proof-of-Ears: "noticed [one specific curatorial choice] — built something for that slot"
    GOOD: "noticed you mix tribal percussion with downtempo exits — have something for that gap"
  Credibility Leakage: "[social proof] / [track] / [micro-genre]"
    GOOD: "290K IG / Renamed / tribal psytrance"

DEAD SUBJECTS: anything formatted like "[Track] ([BPM] BPM) for [Playlist Name]" — that is a form submission, not a message.

OPENING — show you listened AND connect it to your own work:
The observation must land in your world, not just cite theirs. Not "you added [track X] — great taste." Instead: "You kept [track X] at position 4 for four months — that patience is what I tried to build into Renamed." Research + how it connects to your music = a person, not a bot.
If no research: go straight to the sonic match. "Renamed sits at 130 BPM in the tribal space — same register as what you've been building toward." Honest generic beats fabricated specific.
  DEAD: "I love your playlist and think your curation is really unique."
  DEAD: "I've been listening to your playlist for a while."

THE TRACK — music writing, not a press release:
Name + BPM + one line that sounds like a listener wrote it, not a publicist. Then the link inline.
  GOOD: "Jericho — 140 BPM — percussion that builds like a siege and breaks exactly when it should → [link]"
  GOOD: "Renamed — 130 BPM — I buried a Hebrew text in the low end. Three months before anyone mentioned it → [link]"
  DEAD: "My track Jericho is 140 BPM and would be a great fit for your playlist → [link]"

BRIDGE (optional — only if genuinely true):
One sentence connecting their existing taste to what you're sending. Skip if you're reaching.
  GOOD: "Sits in the same register as [Track X] you added in February — same patience, different scripture."

THE CLOSE — vary it, keep it short, keep it peer-to-peer:
  "Worth one listen?"
  "I'm curious what you make of the percussion architecture."
  "Worth 90 seconds if the BPM sits right."
  "Does this sit in the same register as what you've been building toward?"
  DEAD: "I would be so honored if you would consider adding it to your wonderful playlist."
  DEAD: "I hope this is a good fit and look forward to hearing from you!"

SIGNATURE (exact format):
Robert-Jan
robertjanmastenbroek.com  |  @holyraveofficial (290K)  |  Tenerife, CET
(290K in the signature, not the body — signals discovery upside quietly, not as a barter offer.)

FINAL CHECK:
□ Subject: tension or specificity? (not a form submission)
□ Opening: could a bot write this? (if yes, rewrite — add the connection to your work)
□ Track line: music writing or press release copy?
□ Human Moment: one line that could only come from RJM's life (see STORY FRAGMENTS)
□ Spotify link: inline with the track, not on its own line
□ Close: selector tone or supplicant tone?
□ Word count: 55–80 body words
""",

    "label": """
═══ LABEL EMAIL — COMPLETE SYSTEM ═══

READER PSYCHOLOGY:
Label A&R reads 40+ demos a week. Their primary fear: "artist with no angle." They need a story their marketing team can package. Secondary fear: catalogue lock-in with an artist with no proven market. Both fears must be defused before the music is mentioned.

SUBJECT LINE:
2–4 words lowercase. Feel like an internal Slack message, NOT a pitch. Reference their catalogue or a specific recent move.
  GOOD: "after the [artist] signing"
  GOOD: "re: your recent [genre] direction"
  DEAD: "demo from robert-jan mastenbroek"
  DEAD: "music submission"

OPENER: State one specific researched observation about a signing choice, release strategy, or public statement that reveals their A&R taste. Connect to what it implies about what they VALUE — not how great they are.
  GOOD: "Signing [Artist] the month after their Boiler Room set — before the blog cycle — tells me you move on feel, not metrics."
  DEAD: "I really admire what you've built with [Label]."

TRANSITION: Bridge their taste to RJM's relevance through contrast or paradox.
  Template: "What I'm making lives in that same [adjective] territory — [specific sonic descriptor] — built without a label, which is either a problem or an asset depending on how you read it."

CHALLENGE: Name their specific ROSTER GAP — inferred from the trigger observation. Not a generic industry problem.
  GOOD: "Your psytrance roster is technically strong but the spiritual narrative angle — the thing that turns a set into a pilgrimage — is missing."

VALUE PROP — commercial hook first, story second:
  1. Owns all 30+ masters outright — walked away from a deal at 21 to keep them.
  2. 290K Instagram (@holyraveofficial) built independently, zero label infrastructure.
  3. The story arc (€6M platform, total collapse, rebuilt in Tenerife) is pre-packaged marketing narrative — real, verified, documentable.
State matter-of-factly. NEVER use: "unique," "authentic," "journey," "passion," "vibe."

HIDDEN OBJECTION DEFUSE:
Frame this as a licensing/release conversation, not a demo begging for a deal. RJM is approaching as an IP holder, not an unsigned artist.
  Adjust by label type:
  - Sync/licensing label: lead with master ownership + sync potential
  - Boutique imprint: lead with marketing narrative + fanbase size
  - Artist-run label: peer relationship + IP collaboration angle

CLOSE — about their roster logic, not whether they want to "hear more":
  GOOD: "Is there a sonic territory you're actively looking to add to the roster right now?"
  GOOD: "Does the owned-masters angle change how you'd approach a conversation like this?"
  DEAD: "Would you be open to a call to discuss further?"

TONE: Peer-to-peer. Robert-Jan is not auditioning — he is identifying mutual interest. Write with that confidence.
LENGTH: 120–160 words maximum.
""",

    "festival": """
═══ FESTIVAL BOOKING — COMPLETE SYSTEM ═══

READER PSYCHOLOGY:
Festival bookers receive hundreds of booking requests. They are not looking for the best DJ — they already have those. They are looking for the artist who completes THEIR STORY. The lineup is a narrative. Robert-Jan's job: show how he fits the chapter they haven't written yet. Their primary fear: "this artist looks fine but gives us nothing to say about why they're here."

THE ANGLE IS ALWAYS THE SAME — ONLY THE FRAMING CHANGES:
"A rave becomes holy wherever Robert-Jan plays" works for every festival type.
- Conscious/spiritual events: lead with it directly
- Secular/underground events: let it arrive as a provocation in the transition sentence
- NEVER suppress it entirely — it IS the differentiator

SUBJECT LINE: 2–4 words lowercase. Reference the festival or a specific edition — never the artist's name.
  GOOD: "re: [festival name] 2026 lineup"
  GOOD: "after [headliner name] sunrise set"
  DEAD: "festival booking — robert-jan mastenbroek"
  DEAD: "available for shows"

OPENER: Reference ONE specific, researched element: a headliner choice, stage concept, lineup decision, stated ethos, or past edition moment. The observation must reveal you understand what they're BUILDING, not that you admire it.
  GOOD: "Programming Kalya Scintilla on the closing sunrise set — not the main stage — tells me you think about emotional arc, not just draw."
  GOOD: "The 'no phones on the dancefloor' policy is the first booking criterion I've seen that makes artistic sense."
  DEAD: "I've always loved the curation at [Festival]."

TRANSITION — plant the FORWARDING LINE here (the sentence a booker copies into their internal lineup brief):
  GOOD: "What I play is electronic music that behaves like a ceremony — which is either exactly what [festival] programs or the thing it's been missing."
  GOOD: "The rave becomes holy wherever I play — which makes [festival] either a natural home or an interesting provocation."

CHALLENGE: Name their lineup gap, inferred from the trigger observation.
  GOOD: "The tribal-psytrance slot on your second stage has been filled by technically competent artists — none of them bring a reason to be THERE."
  GOOD: "Your sunrise sets have a sound but not yet a story."

VALUE PROP — in order: story → proof → logistics:
  Story: "At 21, I walked away from a record deal to keep my masters. At 30, I lost a €6M platform to a hack and blackmail. I rebuilt in Tenerife, own 30+ tracks, and play sets rooted in that arc."
  Proof: "290K Instagram (@holyraveofficial). Crowd footage runs to 1.9M views on IG without paid promotion."
  Logistics: "Based in Tenerife (Canary Islands, Spain) — EU routing, Canary Islands travel rates, full European festival season available."

HIDDEN OBJECTION — plant the lineup-announcement sentence:
The sentence they'd use to justify the booking internally.
  GOOD: "If the question is 'how do we explain this to the audience' — 'Dutch DJ who walked away from a label deal, lost everything, and builds ceremony into every set' is a one-line brief."

CLOSE — about their programming LOGIC, not a meeting ask:
  GOOD: "Is the sunrise slot something you build by artist or by sound?"
  GOOD: "Does the tribal-psytrance category have room in the 2026 build?"
  DEAD: "Would love to jump on a call."

LINEUP ANNOUNCEMENT LINE: Every email must contain one sentence that could be copy-pasted into an internal lineup brief or Instagram announcement. No special formatting — just make sure it's there.
LENGTH: 130–170 words.
""",

    "podcast": """
═══ PODCAST GUEST PITCH — COMPLETE SYSTEM ═══

READER PSYCHOLOGY:
Podcast hosts read pitches for one thing: an episode they could NOT manufacture with a standard guest. Their fear: "this episode sounds like 40 others I've made." RJM is valuable because his story combination is STRUCTURALLY RARE — not because any single element is impressive. The pitch must deliver the combination as a PARADOX, not a list.

THE PARADOX FRAME (engine of every pitch):
Robert-Jan holds positions most people believe are mutually exclusive:
- Sober raver who plays 140 BPM psytrance sets
- Jesus-committed artist whose music plays in clubs, not churches
- Man who built €6M and lost it all — calls the collapse the best thing that happened to him, and can prove why without spiritual bypassing
- Dutch producer who walked away from a label deal at 21, owns every master, 290K followers with zero industry infrastructure

The pitch: not "here is a guest with an interesting story" but "here is a guest whose existence creates an argument your audience will still be having after the episode ends."

SUBJECT LINE — never start with "GUEST PITCH: [Name], [Title], [Topic]" (PR agency spam):
  FORMULA A (highest priority — Reversal Fragment):
    "[age/location] / [one-line reversal of fortune]"
    GOOD: "36 / Tenerife / built €6M, lost it all, the music is better now"
    GOOD: "lost everything at 34 — the album I made after is the best work of my life"
  FORMULA B (Theological Tension):
    "[genre] + [belief system] — why these don't cancel each other"
    GOOD: "psytrance and Jesus — not a contradiction (here's the framework)"
    GOOD: "rave as church — one DJ's serious argument"
  FORMULA C (Falsifiable Claim):
    "[bold industry claim] — [proof in 3 words]"
    GOOD: "Spotify punishes trend-chasers — I stopped and grew"
    GOOD: "no label, no manager, 290K — here's what that actually costs"

ANGLE SELECTION — pick by researching what the HOST has said they're missing, not just by podcast category:
  Faith/ministry hosts: Lead with the RETURN to faith (not conversion). "I kept making rave music and the ravers came." Collapse → surrender → rebuild.
  Music business hosts: Lead with the masters decision at 21. Most radical choice an artist can make.
  Entrepreneur/resilience hosts: Lead with the MECHANICS of the €6M collapse — hack, blackmail, what that does to a person at 30. The unusual element: he repaid every creditor. That separates "lost everything" stories (common) from "took responsibility and rebuilt" stories (rare).
  Expat/nomad hosts: Lead with identity destruction in Holland and what it takes to rebuild a name from zero. Tenerife = strategic, not escapism.
  Sober/wellness hosts: Lead with the sobriety paradox — sober raver, in club settings, makes the case that the altered state IS the music.

OPENER: Quote or reference ONE specific episode — a verbatim line the host said, or a specific episode title and what it revealed about what the host values.
  DEAD: "I've been a listener for years" / "Love what you do" / "Your show resonates with me"

TRANSITION — the sentence the host screenshots and sends to their producer. Slightly provocative, not aggressive:
  GOOD: "I make tribal psytrance for ravers, own every master, and haven't touched alcohol in [X] years — and I think that combination is an episode your audience hasn't heard."
  GOOD: "I'm a Dutch DJ who lost €6M and found Jesus — not as a redemption arc, just as the actual order of events."

HUMAN MOMENT — one image, never a list:
The story works because of ONE specific detail, not because of the full arc. "I was on my knees in a camper van on the south coast of Tenerife" is an image. "I went through a difficult period and discovered my faith" is a category. Choose the image. Let the host's imagination complete the arc.

HIDDEN OBJECTION — scarcity framing, not "I'm different":
  GOOD: "If your inbox has another guest who walked away from a label deal at 21, lost €6M to blackmail at 30, and still credits Jesus without running a ministry — book them first. I'll wait."

CLOSE — make the host feel they're missing a specific episode if they don't reply:
  GOOD: "Has anyone on your show walked away from a deal and kept the faith while the money burned?"
  GOOD: "Is the 'sober in the rave' angle something your audience has asked about, or is it a gap nobody's named yet?"
  DEAD: "Would I be a good fit for your show?" / "Happy to jump on a call"

Always end with: "Available via Zoom, video or audio."
LENGTH: 140–180 words maximum.
""",

    "youtube": """
═══ YOUTUBE CHANNEL PITCH — COMPLETE SYSTEM ═══

THE OFFER (drives every word):
RJM gives a free, full-quality WAV + cover art. The channel uploads it, runs ads, keeps 100% of ad revenue. Content ID has been turned off at the distributor — no claims, no split. The only return: RJM's Spotify artist link in the description so listeners can stream.

THE EXCHANGE: Channel gets a content asset that earns forever. RJM gets discovery traffic.

SUBJECT LINE: Lead with the business proposition. Track metadata identifies fit but the subject is opened because of the deal.
  FORMULA: "Free track upload — 100% ad rev yours | [Track] ([BPM] BPM [genre])"
  GOOD: "Free track upload — 100% ad rev yours | Jericho (140 BPM tribal psytrance)"
  DEAD: "Kavod (140 BPM Hebrew psytrance) — free track, you keep 100% ad rev"  ← buries the money

OPENER (2 sentences — the ONLY personalized part):
Reference ONE specific thing that proves you watched their channel. Not the channel name, not the genre description — one concrete detail: a recent upload's TITLE, a specific edit choice, BPM they consistently use, visual treatment of thumbnails.
  NEVER write:
  - "I really love what you're doing"
  - "Your channel has great energy"
  - "I've been following your work"
  If no research: skip personalisation, go straight to BPM/genre fit — honest generic beats fabricated specific.

TRACK OFFER (2–3 sentences):
  - Name the track, BPM, genre, and ONE visual word (what does this music look like?)
  - Include Spotify link IMMEDIATELY after the track title — never name the track without it
  - State that WAV + artwork are ready to send

THE DEAL (2 sentences — no hedging, no softening):
  Sentence 1: "You keep 100% of the ad revenue — Content ID is disabled on this track."
  Sentence 2: "The only thing I ask is my Spotify artist link in the description, so your listeners can stream."
  NEVER write:
  - "I just ask" (weakens the ask)
  - "monetize" (wrong frame)
  - "hopefully" or "if you're interested" (hedging)
  - Any reference to distributors, BandLab, or claims processes

CTA (1 sentence): "Reply 'yes' and I'll send the WAV + artwork within the hour."

SIGNATURE: Robert-Jan | robertjanmastenbroek.com | 290K IG @holyraveofficial

TONE: Peer-to-peer. Two professionals making a deal. Not a fan writing to a creator.
FAITH ANGLE: OFF unless channel description explicitly uses: christian, worship, faith, spiritual.
LENGTH: 80–110 words.
""",
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
FAITH-FOCUSED CONTACT: Lead with faith openly — it's the story, not a footnote.
Tone: warm, genuine, human. Not a pitch. Not a testimony on a stage. More like: talking to someone at a church you both showed up to.
The hook is not "Jesus-loving raver" as a brand statement — it's the specific moment. "I was on my knees in Tenerife. That's where it changed." One image. Let it breathe.
Biblical track titles and Hebrew lyrics are details, not selling points — mention them because they're true and interesting, not because they're marketable.
Churches as venues: the music reaches people who wouldn't walk into a church. That's not a value prop — it's just what happens. Say it plainly.
Banned in this context: "passionate worship experience" / "spirit-filled" / "anointed" / "this aligns with your ministry vision" — sound like a person, not a ministry brochure."""


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


_FOLLOWUP_SYSTEM = """You write follow-up emails as Robert-Jan Mastenbroek (Dutch DJ/producer, Tenerife). First person. Peer-to-peer posture.

POSTURE RULE (read before writing a single word):
You are not apologizing for emailing. You are not asking if they got the last email. You are not "just checking in." You are re-entering because something has changed, something new is available, or the window is closing. Every follow-up must have a reason to exist that is NOT "I didn't hear back."

HUMAN VOICE RULE:
A follow-up should feel like a genuine addition — something you actually noticed, not a process step. "Wanted to add this" beats "following up on my previous email." One new thing, told like a person would tell it. "The track landed on [playlist] last week — thought you'd want to know." / "Almost deleted this track twice. It hit 40K streams this month." One image, one fact, one honest thought. Not a strategy.

Sign off: Robert-Jan | robertjanmastenbroek.com | https://instagram.com/robertjanmastenbroek"""


def _build_followup_prompt(contact: dict, is_second: bool = False) -> str:
    orig_subject = contact.get("sent_subject", "") or ""
    # Prefer full stored body, fall back to snippet; strip whitespace-only values
    orig_body    = (contact.get("sent_body") or contact.get("sent_body_snippet", "") or "").strip()
    name         = contact.get("name", "")
    ctype        = contact.get("type", "curator")
    genre        = contact.get("genre", "") or ""
    notes        = contact.get("notes", "") or ""

    subject_line = f"Re: {orig_subject}" if orig_subject else ""

    if is_second:
        # ── FOLLOWUP FINAL (21 days) — last contact, genuine urgency ───────────
        mode_instructions = """MODE: FINAL CONTACT (last email — 21 days since first send)

JOB: Close the loop with genuine warmth and one last clean shot. No resentment. No performance of urgency.
TONE: Calm, real, direct. Like telling a friend you're moving on — no drama, just honesty.

HOW IT FLOWS:
Open by acknowledging the silence simply — not with blame, not with passive aggression. "I'll take the silence as a no for now — no hard feelings." That's the tone: a person, not a process.

Then restate the offer in its most concrete form — lead with what's in it for them, not with yourself.

Close with ONE specific, time-limited reason to act. Use whichever is true:
  - Content ID: "I'm re-enabling Content ID on [track] at end of [month] — after that, the 100% revenue offer is gone."
  - Exclusivity: "Keeping this offer open to one channel in the [genre] space — whoever responds first."
  - New track: "New track dropping soon — happy to offer both, but only to whoever's in first."

Then step back. No postscript. No "no worries if not." Just done.

NEVER: "I just wanted to follow up one last time" / manufactured countdown / "Sorry to bother you again."
LENGTH: 60–80 words."""

    else:
        # ── FOLLOWUP #1 (7-10 days) — bring ONE new piece of information ────────
        mode_instructions = """MODE: FOLLOW-UP #1 (7–10 days after first email)

JOB: Add one genuine thing. Not "did you see my last email" — something that actually happened or something you actually noticed.

ONE NEW THING to bring (choose whichever is true):
  - A Spotify milestone: "The track hit [X] streams this week."
  - A playlist placement: "It landed on [playlist] since I wrote."
  - An honest observation: "Almost deleted this track twice — it's the one that keeps getting found."
  - A second track if the first was a genre mismatch.
  - A real window closing: "Keeping Content ID off through end of month."

HOW IT FLOWS:
Start with a callback to the most memorable line from the original email — not a recap, a reference. One sentence. Then the new thing. Then a short, frictionless ask. It should read like a quick message, not a scheduled follow-up sequence.

LENGTH: 50–70 words. Shorter than the original."""

    if orig_body:
        context_section = f"Original email body (mine the most compelling line for the callback):\n{orig_body}\n"
    else:
        context_section = (
            f"Contact type: {ctype}\nGenre / focus: {genre}\nNotes: {notes}\n"
            f"(Original email body not stored — write a natural follow-up grounded in the "
            f"contact's type and genre. Do NOT invent details that were not sent.)\n"
        )

    subject_directive = (
        f'Subject: "{subject_line}"'
        if subject_line
        else "Subject: write a short, specific subject (2–4 words lowercase, no punctuation)"
    )

    user = f"""Follow-up to {name} ({ctype}).
{context_section}
{mode_instructions}
{subject_directive}

Return ONLY valid JSON with 'subject' and 'body' keys."""

    return _FOLLOWUP_SYSTEM + "\n\n" + user


def _get_track_recs(ctype: str, genre: str, notes: str) -> str:
    """
    Track priority: 80% Renamed (130 BPM tribal) + Halleluyah (140 BPM psytrance).
    Organic house / nomadic-electronic tracks (Living Water etc.) only for
    explicitly organic/house/accessible contexts. Default fallback always returns
    Renamed + Halleluyah — never organic house as default.

    Tracks with empty spotify URLs are silently filtered (placeholders like Kavod
    before its URL is pasted into story.py). The brand rule requires "never name
    a track without its Spotify link", so empty-URL tracks cannot be recommended.

    For ctype='youtube', the psytrance branch is forced regardless of genre — all
    channels in the YouTube pipeline are filtered to psytrance/tribal/progressive
    at discovery time, so organic-house / nomadic-electronic tracks are never the
    right pitch here.
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
        primary   = TRACKS["psytrance"]    if is_psy    else TRACKS["organic_tribal"]
        secondary = TRACKS["organic_tribal"] if is_psy    else TRACKS["psytrance"]
        for t in primary + secondary:
            if not _has_url(t):
                continue
            entry = _fmt(t)
            if entry not in lines:
                lines.append(entry)

    if is_faith:
        for t in TRACKS["organic_house"]:
            if not _has_url(t):
                continue
            entry = _fmt(t)
            if entry not in lines:
                lines.append(entry)

    if is_melodic and not is_psy and not is_tribal:
        for t in TRACKS["organic_house"]:
            if not _has_url(t):
                continue
            entry = _fmt(t)
            if entry not in lines:
                lines.append(entry)

    # Default: Renamed + Halleluyah — never fall back to slower organic house by default
    if not lines:
        for t in TRACKS["organic_tribal"] + TRACKS["psytrance"]:
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
    rejected = 0
    for item in items:
        email   = item.get("email", "").strip()
        subject = item.get("subject", "").strip()
        body    = item.get("body", "").strip()
        if not email or not subject or not body:
            continue
        body = _inject_spotify_links(body)
        body = _ensure_signature(body)
        if _BRAND_GATE_AVAILABLE:
            try:
                validation = _brand_gate.validate_content(body) or {}
            except Exception as exc:
                log.warning("brand_gate.validate_content failed for %s: %s", email, exc)
                validation = {}
            if not validation.get("passes", True):
                log.warning("Brand gate rejected batch email for %s (score %s): %s",
                            email, validation.get("score"), validation.get("flags"))
                rejected += 1
                continue
        result[email] = (subject, body)
        log.info("Batch generated — %s subject: %r", email, subject)

    log.info("Batch complete: %d/%d emails generated (%d brand-gate rejected)",
             len(result), n, rejected)
    return result


def generate_followup_email(contact: dict, is_second: bool = False) -> tuple[str, str]:
    """
    Generate a short follow-up email. Returns (subject, body).
    Uses the fast/cheap model — follow-ups are short and simple.

    Args:
        contact:   Contact dict with sent_subject, sent_body, type, genre, etc.
        is_second: True for the final follow-up (21 days, urgency close);
                   False for the first follow-up (7–10 days, new information hook).
    """
    prompt = _build_followup_prompt(contact, is_second=is_second)

    mode = "final" if is_second else "first"
    log.info("Generating %s follow-up for %s...", mode, contact.get("email"))

    raw = _call_claude(prompt, model=CLAUDE_MODEL_FAST)
    subject, body = _parse_response(raw)

    # Force Re: prefix when we have the original subject
    orig_subject = contact.get("sent_subject", "") or ""
    if orig_subject and not subject.lower().startswith("re:"):
        subject = f"Re: {orig_subject}"

    log.info("Generated %s follow-up — subject: %r", mode, subject)
    if _BRAND_GATE_AVAILABLE:
        _brand_gate.gate_or_warn(body, context=f"template_engine.followup_{mode}")
    return subject, body
