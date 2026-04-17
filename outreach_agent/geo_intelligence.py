"""
geo_intelligence.py — Geographic zone logic for relationship-first outreach.

Determines whether a contact is in range for booking, collaboration, or
relationship-only outreach based on their location and RJM's home base (Tenerife).

Zones:
  home     — Tenerife, Canary Islands, Spain
  primary  — Netherlands, Ibiza
  europe   — All European countries
  worldwide — Everything else

Rules by outreach_goal:
  booking      — all zones, framing changes (local / travel / future)
  collaboration — home only (photographers, visual artists)
  relationship  — worldwide, no restriction
  music_share   — worldwide, no restriction
"""

from __future__ import annotations

# ── Zone definitions ──────────────────────────────────────────────────────────

_HOME = [
    "tenerife", "canary islands", "canarias", "santa cruz", "las palmas",
    "gran canaria", "lanzarote", "fuerteventura", "la palma", "spain",
    "españa", "madrid", "barcelona", "seville", "sevilla", "valencia",
    "malaga", "málaga", "bilbao",
]

_PRIMARY = [
    "netherlands", "nederland", "amsterdam", "rotterdam", "den haag",
    "the hague", "utrecht", "eindhoven", "groningen", "netherlands",
    "ibiza", "formentera", "balearic", "baleares",
]

_EUROPE = [
    # UK
    "uk", "united kingdom", "england", "london", "manchester", "bristol",
    "edinburgh", "glasgow", "birmingham",
    # Germany
    "germany", "deutschland", "berlin", "hamburg", "munich", "münchen",
    "cologne", "köln", "frankfurt", "stuttgart", "düsseldorf",
    # France
    "france", "paris", "lyon", "marseille", "toulouse", "bordeaux",
    # Belgium
    "belgium", "belgique", "brussels", "brussel", "antwerp", "ghent",
    # Switzerland
    "switzerland", "schweiz", "zurich", "zürich", "geneva", "basel",
    # Austria
    "austria", "österreich", "vienna", "wien", "graz", "salzburg",
    # Italy
    "italy", "italia", "rome", "roma", "milan", "milano", "florence",
    "firenze", "naples", "napoli", "turin", "torino", "sardinia",
    # Portugal
    "portugal", "lisbon", "lisboa", "porto", "algarve",
    # Scandinavia
    "sweden", "sverige", "stockholm", "gothenburg", "göteborg", "malmö",
    "norway", "norge", "oslo", "bergen", "trondheim",
    "denmark", "danmark", "copenhagen", "københavn", "aarhus",
    "finland", "suomi", "helsinki", "tampere",
    "iceland", "reykjavik",
    # Eastern Europe
    "poland", "polska", "warsaw", "warszawa", "kraków", "wrocław",
    "czech republic", "czechia", "prague", "praha", "brno",
    "hungary", "magyarország", "budapest",
    "romania", "românia", "bucharest", "bucurești", "cluj",
    "bulgaria", "sofia",
    "croatia", "hrvatska", "zagreb", "split", "dubrovnik",
    "serbia", "belgrade", "beograd",
    "greece", "hellas", "athens", "athina", "thessaloniki",
    "turkey", "türkiye", "istanbul", "ankara",
    # Baltic
    "estonia", "tallinn", "latvia", "riga", "lithuania", "vilnius",
    # Other European
    "ireland", "dublin", "cork",
    "scotland", "wales",
    "luxembourg", "liechtenstein", "monaco", "malta",
    "slovakia", "bratislava", "slovenia", "ljubljana",
    "north macedonia", "skopje", "albania", "tirana",
    "bosnia", "sarajevo", "montenegro", "podgorica",
    "moldova", "chisinau",
    "ukraine", "kyiv", "kiev", "lviv",
    "belarus", "minsk",
    "cyprus", "nicosia",
    "europe", "european",
]

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

# ── Zone classification ───────────────────────────────────────────────────────

def classify_zone(location: str) -> str:
    """Return 'home', 'primary', 'europe', or 'worldwide'."""
    if not location:
        return "worldwide"
    loc = location.lower()
    if any(p in loc for p in _HOME):
        return "home"
    if any(p in loc for p in _PRIMARY):
        return "primary"
    if any(p in loc for p in _EUROPE):
        return "europe"
    return "worldwide"


# ── Booking reachability ──────────────────────────────────────────────────────

def booking_framing(location: str) -> str:
    """
    Returns framing hint for booking outreach:
      'local'  — same zone, pitch directly
      'travel' — Europe, frame as "I travel Europe frequently"
      'future' — Worldwide, frame as "if you ever need a DJ internationally"
    """
    zone = classify_zone(location)
    if zone in ("home", "primary"):
        return "local"
    if zone == "europe":
        return "travel"
    return "future"


# ── Collaboration reachability ────────────────────────────────────────────────

def collab_reachable(location: str) -> bool:
    """
    Photography / visual collab — Tenerife-local only.
    Returns False for contacts outside the home zone.
    """
    return classify_zone(location) == "home"


# ── Goal resolution ───────────────────────────────────────────────────────────

def resolve_goal(persona: str, location: str) -> str:
    """
    Resolve the effective outreach_goal for a persona + location.

    Collaboration personas are downgraded to 'relationship' when the contact
    is outside the home zone — don't pitch a photo shoot to someone 2000 km away.
    """
    base = PERSONA_GOALS.get(persona, "relationship")
    if base == "collaboration" and not collab_reachable(location or ""):
        return "relationship"
    return base


# ── Discovery query geo-targeting ────────────────────────────────────────────

# Ordered priority list of regions for booking-type discovery queries.
# Rotate through these across runs to spread geographic coverage.
BOOKING_REGIONS = [
    # Home / Primary first
    "Tenerife OR 'Canary Islands'",
    "Netherlands OR Amsterdam",
    "Spain",
    "Ibiza",
    # Europe
    "UK OR London",
    "Germany OR Berlin",
    "France OR Paris",
    "Belgium OR Brussels",
    "Portugal OR Lisbon",
    "Italy OR Milan",
    "Switzerland OR Zurich",
    "Sweden OR Stockholm",
    "Norway OR Oslo",
    "Denmark OR Copenhagen",
    "Poland OR Warsaw",
    "Czech Republic OR Prague",
    "Hungary OR Budapest",
    "Greece OR Athens",
    "Austria OR Vienna",
    "Ireland OR Dublin",
]

# For relationship/music-share personas — worldwide markets to rotate through.
WORLDWIDE_MARKETS = [
    "USA", "Canada", "Australia", "Brazil", "Argentina",
    "South Africa", "Israel", "Japan", "Mexico", "Colombia",
    "New Zealand", "India", "Indonesia", "Singapore",
]
