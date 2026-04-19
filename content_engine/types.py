from dataclasses import dataclass, field
from enum import Enum
from typing import Literal
import json
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent


@dataclass
class TrendBrief:
    date: str
    top_visual_formats: list
    dominant_emotion: str
    oversaturated: str
    hook_pattern_of_day: str
    contrarian_gap: str
    trend_confidence: float

    def save(self):
        out = PROJECT_DIR / "data" / "trend_brief" / f"{self.date}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(self.__dict__, indent=2))

    @classmethod
    def load(cls, date: str) -> "TrendBrief":
        path = PROJECT_DIR / "data" / "trend_brief" / f"{date}.json"
        return cls(**json.loads(path.read_text()))

    @classmethod
    def load_today(cls) -> "TrendBrief":
        from datetime import date
        return cls.load(date.today().isoformat())


@dataclass
class OpeningFrame:
    clip_index: int
    source: str  # "ai_generated" | "footage"
    source_file: str
    emotion_tag: str
    visual_category: str
    footage_score: float


@dataclass
class PerformanceRecord:
    post_id: str
    platform: str
    clip_index: int
    variant: str
    hook_mechanism: str
    visual_type: str
    clip_length: int
    format_type: str = ""
    hook_template_id: str = ""
    hook_sub_mode: str = ""
    transitional_category: str = ""
    transitional_file: str = ""
    track_title: str = ""
    views: int = 0
    completion_rate: float = 0.0
    scroll_stop_rate: float = 0.0
    share_rate: float = 0.0
    save_rate: float = 0.0
    recorded_at: str = ""


@dataclass
class PromptWeights:
    hook_weights: dict
    visual_weights: dict
    best_clip_length: int
    best_platform: str
    updated: str

    @classmethod
    def defaults(cls) -> "PromptWeights":
        return cls(
            hook_weights={"tension": 1.0, "identity": 1.0, "scene": 1.0, "claim": 1.0, "rupture": 1.0},
            visual_weights={"ai_generated": 1.0, "performance": 1.0, "b_roll": 1.0, "phone": 1.0},
            best_clip_length=15,
            best_platform="instagram",
            updated="",
        )

    def save(self):
        path = PROJECT_DIR / "prompt_weights.json"
        path.write_text(json.dumps(self.__dict__, indent=2))

    @classmethod
    def load(cls) -> "PromptWeights":
        path = PROJECT_DIR / "prompt_weights.json"
        if not path.exists():
            w = cls.defaults()
            w.save()
            return w
        return cls(**json.loads(path.read_text()))


class ClipFormat(Enum):
    TRANSITIONAL = "transitional"
    EMOTIONAL = "emotional"
    PERFORMANCE = "performance"
    SACRED_ARC = "sacred_arc"


@dataclass
class TransitionalHook:
    """A pre-cleared bait clip for transitional hook format."""
    file: str
    category: str
    duration_s: float
    last_used: str | None
    performance_score: float
    times_used: int


@dataclass
class TrackInfo:
    """A track in the active pool with auto-populated facts."""
    title: str
    file_path: str
    bpm: int
    energy: float
    danceability: float
    valence: float
    scripture_anchor: str
    spotify_id: str
    spotify_popularity: int
    pool_weight: float
    entered_pool: str


@dataclass
class UnifiedWeights:
    """Expanded weights covering all learning dimensions."""
    hook_weights: dict
    visual_weights: dict
    format_weights: dict
    platform_weights: dict
    transitional_category_weights: dict
    track_weights: dict
    best_clip_length: int
    best_platform: str
    updated: str
    # New dimensions: variant stratification + time-of-day bucket.
    # Default empty so old on-disk UnifiedWeights files still load.
    sub_mode_weights: dict = None          # COST/NAMING/RUPTURE/etc. → weight
    time_of_day_weights: dict = None       # morning/midday/evening/late → weight
    best_time_of_day: str = "morning"

    def __post_init__(self):
        if self.sub_mode_weights is None:
            self.sub_mode_weights = {}
        if self.time_of_day_weights is None:
            self.time_of_day_weights = {}

    @classmethod
    def defaults(cls) -> "UnifiedWeights":
        return cls(
            hook_weights={
                "tension": 1.0, "identity": 1.0, "scene": 1.0,
                "claim": 1.0, "rupture": 1.0,
            },
            visual_weights={
                "performance": 1.0, "b_roll": 1.0, "phone": 1.0,
            },
            format_weights={
                "transitional": 1.0, "emotional": 1.0, "performance": 1.0,
                "sacred_arc": 1.0,
            },
            platform_weights={
                "instagram": 1.0, "youtube": 1.0, "facebook": 1.0,
                "instagram_story": 1.0, "facebook_story": 1.0,
            },
            transitional_category_weights={
                "nature": 1.0, "satisfying": 1.0, "elemental": 1.0,
                "sports": 1.0, "craftsmanship": 1.0, "illusion": 1.0,
            },
            track_weights={},
            best_clip_length=15,
            best_platform="instagram",
            updated="",
            sub_mode_weights={
                "COST": 1.0, "NAMING": 1.0, "DOUBT": 1.0, "DEVOTION": 1.0,
                "RUPTURE": 1.0, "FINDER": 1.0, "PERMISSION": 1.0,
                "RECOGNITION": 1.0, "SEASON": 1.0, "UNSAID": 1.0,
                "BODY": 1.0, "TIME": 1.0, "GEOGRAPHY": 1.0,
                "THRESHOLD": 1.0, "DISSOLUTION": 1.0,
            },
            time_of_day_weights={
                "morning": 1.0, "midday": 1.0, "evening": 1.0, "late": 1.0,
            },
            best_time_of_day="morning",
        )

    def save(self, path=None):
        path = path or (PROJECT_DIR / "prompt_weights.json")
        path.write_text(json.dumps(self.__dict__, indent=2))

    @classmethod
    def load(cls, path=None) -> "UnifiedWeights":
        path = path or (PROJECT_DIR / "prompt_weights.json")
        if not path.exists():
            w = cls.defaults()
            w.save(path)
            return w
        data = json.loads(path.read_text())
        # Backwards compat: old PromptWeights may be on disk
        if "format_weights" not in data:
            w = cls.defaults()
            w.hook_weights = data.get("hook_weights", w.hook_weights)
            w.visual_weights = data.get("visual_weights", w.visual_weights)
            w.best_clip_length = data.get("best_clip_length", w.best_clip_length)
            w.best_platform = data.get("best_platform", w.best_platform)
            w.updated = data.get("updated", w.updated)
            return w
        # Forward-compat: filter to fields the dataclass accepts so old dumps
        # without sub_mode_weights / time_of_day_weights load cleanly.
        allowed = set(cls.__dataclass_fields__.keys())
        filtered = {k: v for k, v in data.items() if k in allowed}
        w = cls(**filtered)
        # Post-load defaults for fields missing from disk
        if not w.sub_mode_weights:
            w.sub_mode_weights = cls.defaults().sub_mode_weights
        if not w.time_of_day_weights:
            w.time_of_day_weights = cls.defaults().time_of_day_weights
        return w
