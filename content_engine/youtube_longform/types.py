"""
Typed data structures for the YouTube long-form pipeline.

Every module consumes and emits these dataclasses — they are the contract
between prompt_builder → image_gen → render → uploader → registry.

Keep this file small. Behavior lives in the sibling modules.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional


# ─── Mood tiers derived from BPM ─────────────────────────────────────────────
# Maps to prompt_builder's genre_mood_hero slot.
MoodTier = Literal[
    "meditative",       # 120–126 BPM — contemplative figure, handpan-under-moonlight
    "processional",     # 127–132 BPM — quiet pilgrimage, caravan, slow desert movement
    "gathering",        # 133–138 BPM — tribal circle, firelight, crowd beginning to move
    "ecstatic",         # 139–145 BPM — full psytrance ceremony, hands raised, dust plume
]


# ─── Genre family — bigger-picture visual culture split ─────────────────────
# Two proven-viral visual DNAs in our niche:
#   - organic_house:   Cafe de Anatolia / Sol Selectas / Bedouin — warm earth
#                      palette, human cultural dress, Middle Eastern ornament,
#                      contemplative/ceremonial mood. 122–138 BPM band.
#   - tribal_psytrance: Goa/tribal psy that stays tribal-ancient rather than
#                      neo-pagan/cosmic (reject New Age). Sacred-geometry
#                      rooted in Temple/Tabernacle rather than OM/yantra.
#                      139–145 BPM band.
GenreFamily = Literal["organic_house", "tribal_psytrance"]


@dataclass(frozen=True)
class TrackPrompt:
    """Output of prompt_builder — everything the image model needs."""

    track_title:        str
    bpm:                int
    genre:              str                # "tribal psytrance", "organic house", etc.
    mood_tier:          MoodTier
    genre_family:       GenreFamily        # Bigger-picture visual DNA routing
    scripture_anchor:   str                # "Joshua 6", may be ""
    scripture_hook:     str                # Rendered visual phrase for the scene
    flux_prompt:        str                # The full positive prompt
    flux_negative:      str                # Negative prompt (the AI-slop blacklist)
    seed:               Optional[int] = None


@dataclass(frozen=True)
class ImageAsset:
    """A single generated image on disk (hero or thumbnail)."""

    role:           Literal["hero", "thumbnail"]
    local_path:     Path
    remote_url:     str                     # fal.ai returned URL; also uploaded to Cloudinary for render
    width:          int
    height:         int
    prompt_used:    str
    variant_index:  int = 0                # 0 for hero; 0/1/2 for thumbnail A/B/C


@dataclass(frozen=True)
class RenderSpec:
    """Input to render.py — tells Cloudinary/Shotstack how to composite."""

    audio_url:          str                 # Publicly reachable URL to the track MP3/WAV
    hero_image_url:     str                 # Publicly reachable URL to the 1920x1080 still
    duration_seconds:   int                 # Full track length
    output_label:       str                 # Slug used in output filename


@dataclass(frozen=True)
class RenderedVideo:
    """Output of render.py — the MP4 ready for YouTube upload."""

    local_path:     Path
    remote_url:     str
    width:          int
    height:         int
    duration:       int
    codec:          str                     # "h264" expected
    audio_codec:    str                     # "aac" expected


@dataclass(frozen=True)
class UploadSpec:
    """Input to uploader.py — video metadata for YouTube Data API v3."""

    video_path:          Path
    thumbnail_paths:     list[Path]          # 1–3 variants; first is primary
    title:               str                 # "Robert-Jan Mastenbroek - Jericho"
    description:         str
    tags:                list[str]
    category_id:         str = "10"          # 10 = Music
    language:            str = "en"
    audio_language:      str = "zxx"         # "zxx" = no linguistic content (instrumentals). Override to "en" for vocals.
    made_for_kids:       bool = False
    license:             Literal["youtube", "creativeCommon"] = "youtube"
    embeddable:          bool = True
    public_stats:        bool = True
    privacy_status:      Literal["private", "public", "unlisted"] = "private"
    publish_at_iso:      Optional[str] = None       # ISO-8601 UTC — requires privacy_status="private"
    notify_subscribers:  bool = True
    playlist_id:         Optional[str] = None        # Optional playlist to append to
    channel_id:          Optional[str] = None        # Holy Rave channel ID (optional override)
    pinned_comment:      Optional[str] = None        # Auto-post a top-level CTA comment
                                                     # from the channel owner. Creator
                                                     # comments rank top of thread by
                                                     # default; pinning in Studio takes
                                                     # 2 taps on mobile (no Data API).


@dataclass
class PublishRequest:
    """Top-level input to publisher.publish_track()."""

    track_title:      str                  # Case-insensitive key into TrackPool
    audio_path:       Optional[Path] = None  # Override auto-resolved path
    publish_at_iso:   Optional[str] = None  # Scheduled publish time (UTC)
    dry_run:          bool = False          # If True: generate assets but don't upload
    skip_image_gen:   bool = False          # If True: reuse existing hero image if present
    force:            bool = False          # Bypass registry dedup (for re-publishes after fixes)
    channel_id:       Optional[str] = None  # Target channel (Holy Rave) override
    notes:            str = ""
    # Motion path — when True, publisher uses motion.TRACK_STORIES[track_title]
    # (or DEFAULT motion story fallback) to generate a keyframe-chain morph
    # loop via Kling O3, then renders the full-track MP4 by looping the chain
    # to match audio duration. Premium path: ~$5.50-$7.50 per publish vs
    # ~$0.15 for stills-only. Use for launch drops where motion matters.
    motion:           bool = False


@dataclass
class PublishResult:
    """Top-level output of publisher.publish_track()."""

    request:         PublishRequest
    prompt:          Optional[TrackPrompt] = None
    hero_image:      Optional[ImageAsset] = None
    thumbnails:      list[ImageAsset] = field(default_factory=list)
    video:           Optional[RenderedVideo] = None
    youtube_id:      Optional[str] = None
    youtube_url:     Optional[str] = None
    smart_link:      Optional[str] = None            # Feature.fm or Odesli URL with UTM
    error:           Optional[str] = None
    elapsed_seconds: Optional[float] = None
    cost_usd:        Optional[float] = None          # Best-effort cost estimate
