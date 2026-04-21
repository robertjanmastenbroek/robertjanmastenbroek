"""
YouTube Long-Form Publisher — Holy Rave still-image + full-track uploads.

Daily-cadence YouTube auto-uploader for RJM's Holy Rave channel. Each upload:
  1. Reads track metadata (BPM, scripture anchor) from content_engine.audio_engine
  2. Builds a Biblically-Nomadic Flux 2 prompt via prompt_builder
  3. Generates 1 hero still (1920x1080) + 3 thumbnail variants (1280x720) on fal.ai
  4. Composites audio + still → MP4 via Cloudinary (Shotstack PAYG fallback)
  5. Uploads to YouTube via Data API v3 + sets thumbnail + adds to BPM playlist
  6. Records dedup row + Feature.fm smart-link UTM for funnel attribution

The module is cloud-native by design — zero local H.264 encoding. See
memory: feedback_no_ffmpeg.md.

Entry point: content_engine.youtube_longform.publisher.publish_track(title)
CLI:        python3 rjm.py content youtube publish <track-title>
"""

__version__ = "0.1.0"

from content_engine.youtube_longform.types import (
    PublishRequest,
    PublishResult,
    RenderSpec,
    TrackPrompt,
    UploadSpec,
)

__all__ = [
    "PublishRequest",
    "PublishResult",
    "RenderSpec",
    "TrackPrompt",
    "UploadSpec",
]
