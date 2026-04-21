"""
RJM Canonical Story — Ground Truth for all email generation.

This module is the single authoritative source the template engine reads.
Edit this file to update how the agent represents Robert-Jan's story.
The agent will NEVER contradict or exaggerate anything here.
"""

# ─── Identity ─────────────────────────────────────────────────────────────────
ARTIST = {
    "full_name":     "Robert-Jan Mastenbroek",
    "email":         "mastenbroekrobertjan@gmail.com",
    "website":       "robertjanmastenbroek.com",
    "instagram":     "https://instagram.com/robertjanmastenbroek",
    # TODO: Replace with your actual Spotify artist ID URL (numeric ID, not handle).
    # Find it by opening your artist profile in Spotify → Share → Copy Link.
    # Format: https://open.spotify.com/artist/4Z90273aBcDeFgH...
    "spotify_artist": "https://open.spotify.com/artist/2Seaafm5k1hAuCkpdq7yds",
    "location":      "Tenerife, Spain",
    "nationality":   "Dutch",
    "age":           36,
}

# ─── Music ────────────────────────────────────────────────────────────────────
MUSIC = {
    "genres":        ["Organic House", "Tribal Psytrance", "Ethnic / Nomadic Electronic", "Middle Eastern Electronic"],
    "bpm_ranges":    ["122–128 BPM (Organic House)", "130 BPM (Organic Tribal House / Middle Eastern)", "140–145 BPM (Tribal Psytrance)"],
    "lyric_source":  "Bible-inspired — ancient scripture embedded in modern electronic production",
    "approach":      "Subtle Salt — the faith anchor is present but not announced. Music first.",
    "total_tracks":  "30+ original tracks on Spotify, all independently released and fully owned",
    "sound_refs":    "Café de Anatolia (Middle Eastern / ethnic house), Sol Selectas / Sabo (tribal-organic house), Bedouin (tribal tech-house), Acid Arab (Middle Eastern electronic), Ace Ventura / Vertex / Aioaska / Symbolic / Ranji / Astrix (melodic to Goa psytrance)",
    "tagline":       "Ancient Truth. Future Sound.",
    "instagram_followers": "290K",
    "label_status":  "Fully independent — no label, no manager, no agent. All masters owned.",
}

# ─── Catalogue (Spotify links) ────────────────────────────────────────────────
# Tracks with empty "spotify" fields are filtered out of email recommendations
# by _get_track_recs() in template_engine.py — so Kavod (below) will activate
# automatically the moment its Spotify URL is pasted here. No code changes needed.
TRACKS = {
    "psytrance": [
        {"title": "Halleluyah",     "bpm": 140, "spotify": "https://open.spotify.com/track/4ysTzCDCezKhxIDOKIV4gG", "notes": "Hebrew lyrics, tribal percussion"},
        {"title": "Kavod",          "bpm": 140, "spotify": "", "notes": "Hebrew 'glory', driving psytrance — PASTE SPOTIFY URL HERE TO ACTIVATE"},
        {"title": "Jericho",        "bpm": 140, "spotify": "https://open.spotify.com/track/2M7cL3KynPGzE1DonuldrN", "notes": "Hebrew lyrics, heavy psytrance energy"},
    ],
    "organic_tribal": [
        {"title": "Renamed",        "bpm": 130, "spotify": "https://open.spotify.com/track/0JDiFWqAa7exA8zh53D4JG", "notes": "English + chanting, ethnic percussion"},
        {"title": "Fire In Our Hands", "bpm": 130, "spotify": "https://open.spotify.com/track/4BGImHDdceYIAWg2MIftfR", "notes": "130 BPM tribal energy"},
    ],
    "organic_house": [
        {"title": "Living Water",   "bpm": 124, "spotify": "https://open.spotify.com/track/4VlJcvP0RvEkzysAkDuKPa", "notes": "Most accessible crossover track"},
        {"title": "He Is The Light","bpm": 122, "spotify": "https://open.spotify.com/track/0tad6gpKfmvHszruHYr7Lm", "notes": "Faith angle most visible"},
        {"title": "You See It All", "bpm": 120, "spotify": "https://open.spotify.com/track/5Vxewgp7pyaTTFa3HDxDSx", "notes": "English lyrics, worship-adjacent"},
    ],
}

# Track recommendations per context
TRACK_RECS = {
    "label":         "All genres relevant — match to label's sound",
    "curator":       "Match BPM/genre to playlist focus",
    "youtube":       "Living Water or He Is The Light — most visual-ready",
    "festival":      "Halleluyah + Jericho for psy stages; Renamed for tribal; melodic tracks for main rooms",
    "podcast_faith": "He Is The Light, You See It All, Living Water — faith angle clearest",
    "podcast_music": "Halleluyah + Jericho or Living Water — strongest production showcase",
    "podcast_story": "Any — the story is the hook, not the specific track",
}

# ─── The Story ────────────────────────────────────────────────────────────────
# Use these story beats — never invent new facts, never exaggerate.
STORY_BEATS = {
    "early": (
        "Grew up in the Netherlands. Was offered a record deal at 21, read the contract, "
        "and walked away — kept his masters and his independence."
    ),
    "entrepreneur": (
        "Built Dream or Donate, which became the largest crowdfunding platform in the Netherlands and Belgium. "
        "€6 million raised through the platform. Was a multi-millionaire by 27."
    ),
    "collapse": (
        "At 30, a hack, a blackmail campaign, and a national media storm in Holland destroyed everything: "
        "businesses, properties, Bitcoin holdings, and his public reputation. "
        "He repaid every cent to every creditor. Lost everything he had built in a decade."
    ),
    "tenerife_campervan": (
        "Chose Tenerife over rebuilding in the Netherlands where his name had been destroyed. "
        "Spent a year living in a camper van on the south coast — performing as a €750/hr vocalist "
        "at weddings and business events. Found more peace than he'd had in years."
    ),
    "faith_turning_point": (
        "Grew up in church from age 10 to 19, then left — not because of God, but because the people "
        "disappointed him. After Tenerife, one night on his knees, alone, he prayed one simple thing: "
        "to be happy again the way he was as a child. That return was the beginning of everything that followed."
    ),
    "music_mission": (
        "His music is where faith and the dance floor meet. Every track has a Biblical anchor — "
        "Hebrew lyrics, Psalms, ancient scripture running through modern electronic production. "
        "Not worship music in the traditional sense. But for him, it is worship. "
        "Mission: ancient truth, carried by future sound, to people who need it most."
    ),
    "now": (
        "Based in Tenerife. 290K Instagram followers. 30+ original tracks, all independently owned. "
        "Releases new music weekly. Hosts free Sunset Sessions every Friday at undisclosed locations in Tenerife. "
        "No label, no manager, no agent. Calls himself a Jesus-loving raver. Means it completely."
    ),
    "identity_marker": (
        "Dutch DJ, producer, and entrepreneur. Built a €6M platform, lost it all at 30, rebuilt in Tenerife. "
        "Makes electronic music with biblical depth. 290K followers, 30+ tracks, no label. "
        "He does not make Christian music. He makes electronic music with Christian depth."
    ),
}

# ─── Brand Rules (enforced in all emails) ─────────────────────────────────────
BRAND_RULES = [
    "Present as 'Robert-Jan Mastenbroek' — full name always",
    "Instagram link is https://instagram.com/robertjanmastenbroek — always use the full URL, never just the handle",
    "Never be preachy. Faith is depth, not the headline (unless writing to faith-focused audiences)",
    "Lead with music to secular audiences. Let the faith angle surface naturally or on request",
    "Never exaggerate. No 'world-class', 'groundbreaking', 'revolutionary' self-description",
    "Be direct and human. No corporate PR language",
    "Emails should feel like they were written by a person who read the recipient's content first",
    "Subject lines: specific, curious, never clickbait",
    "Keep emails under 120 words — hard SMYKM limit, no exceptions",
    "Sign off simply: Robert-Jan — with website and Instagram",
]

# ─── Podcast-specific story hooks ─────────────────────────────────────────────
PODCAST_ANGLES = {
    "faith_and_business": (
        "The arc from building through sheer force of will, to total collapse, to genuine surrender — "
        "and what it actually looks like to rebuild on different ground."
    ),
    "music_business": (
        "A case study in the fully independent model: 21-year-old walks away from a record deal, "
        "owns all his masters, builds a 290K following with no label, no manager, no publicist — "
        "and what the real costs of that path look like."
    ),
    "expat_nomad": (
        "Chose an island after his name was destroyed in Holland. Lived in a camper van for a year. "
        "Built a new life in Tenerife. What you actually leave behind and what you find."
    ),
    "sober_rave": (
        "Makes electronic music as a sober raver. What changes when the music has to carry "
        "the weight of the experience alone — and what he found to fill that space."
    ),
    "creative_redemption": (
        "Losing €6M, a media storm, every possession — and what it takes to rebuild "
        "not just financially but as a creative person with a voice worth using."
    ),
    "faith_and_edm": (
        "At the intersection of psytrance and scripture. Ancient Hebrew in a 140 BPM drop. "
        "What it means to worship at a festival."
    ),
}
