"""
transitional_manager.py — Manage pre-cleared transitional hook clips.

Handles: loading index, picking clips (weighted, cooldown, diversity),
scanning for new clips, updating usage/performance.
"""
import json
import logging
import subprocess
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from content_engine.hook_library import pick_transitional_hook

logger = logging.getLogger(__name__)

PROJECT_DIR = Path(__file__).parent.parent
DEFAULT_HOOKS_DIR = PROJECT_DIR / "content" / "hooks" / "transitional"
CATEGORIES = ["nature", "satisfying", "elemental", "sports", "craftsmanship", "illusion", "viral"]


class TransitionalManager:
    """Manages the transitional hook clip library."""

    def __init__(self, hooks_dir: Optional[Path] = None):
        self.hooks_dir = hooks_dir or DEFAULT_HOOKS_DIR
        self.index_path = self.hooks_dir / "index.json"
        self.bank: list[dict] = []
        self._load()

    def _load(self):
        if self.index_path.exists():
            self.bank = json.loads(self.index_path.read_text())
        else:
            self.bank = []

    def _save(self):
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(json.dumps(self.bank, indent=2))

    def pick(self, yesterday_category: Optional[str] = None, category_weights: Optional[dict] = None) -> Optional[dict]:
        """Pick a transitional hook clip respecting cooldown, diversity, and category weights."""
        if not self.bank:
            logger.warning("Transitional hook bank is empty")
            return None
        return pick_transitional_hook(self.bank, yesterday_category, category_weights=category_weights)

    def mark_used(self, file: str):
        """Mark a clip as used today and save."""
        for h in self.bank:
            if h["file"] == file:
                h["last_used"] = date.today().isoformat()
                h["times_used"] = h.get("times_used", 0) + 1
                break
        self._save()

    def update_score(self, file: str, new_score: float):
        """Update performance score for a clip."""
        for h in self.bank:
            if h["file"] == file:
                h["performance_score"] = new_score
                break
        self._save()

    def full_path(self, file: str) -> Path:
        """Get full filesystem path for a hook clip."""
        return self.hooks_dir / file

    def scan_for_new_clips(self):
        """Scan hooks_dir for MP4/MOV files not yet in the index."""
        existing_files = {h["file"] for h in self.bank}
        for category in CATEGORIES:
            cat_dir = self.hooks_dir / category
            if not cat_dir.exists():
                continue
            for f in cat_dir.iterdir():
                if f.suffix.lower() in (".mp4", ".mov"):
                    rel = f"{category}/{f.name}"
                    if rel not in existing_files:
                        duration = self._get_duration(str(f))
                        self.bank.append({
                            "file": rel,
                            "category": category,
                            "duration_s": duration,
                            "last_used": None,
                            "performance_score": 1.0,
                            "times_used": 0,
                        })
                        logger.info(f"Added new transitional hook: {rel} ({duration:.1f}s)")
        self._save()

    def _get_duration(self, path: str) -> float:
        """Get clip duration via ffprobe."""
        try:
            cmd = [
                "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", path,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            return float(result.stdout.strip())
        except Exception:
            return 3.0  # default assumption

    def health_check(self) -> dict:
        """Check bank health: total clips, per-category, cooldown pool size."""
        total = len(self.bank)
        per_cat = {}
        available = 0
        cooldown_date = date.today() - timedelta(days=7)

        for h in self.bank:
            cat = h["category"]
            per_cat[cat] = per_cat.get(cat, 0) + 1
            if not h["last_used"] or date.fromisoformat(h["last_used"]) < cooldown_date:
                available += 1

        return {
            "total": total,
            "available_after_cooldown": available,
            "per_category": per_cat,
            "healthy": available >= 7,  # need at least 7 for one per day
        }
