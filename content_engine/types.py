from dataclasses import dataclass, field
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
