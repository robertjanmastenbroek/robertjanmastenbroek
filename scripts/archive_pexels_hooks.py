"""One-shot: move Pexels clips to archive/ and rebuild the live index to only viral clips.

Rationale: Pexels-sourced stock footage (the 6 non-viral categories) has zero
evidence of viral CTR, while the 15 clips in viral/ match the proven 20k+ IG
post format. We park the Pexels clips rather than deleting so we can revive
individual clips later if needed.
"""
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).parent.parent
HOOKS = ROOT / "content" / "hooks" / "transitional"
ARCHIVE = HOOKS / "archive"
INDEX = HOOKS / "index.json"

KEEP_CATEGORIES = {"viral"}
ARCHIVE_CATEGORIES = {"nature", "satisfying", "elemental", "sports", "craftsmanship", "illusion"}


def main():
    data = json.loads(INDEX.read_text())
    keep = [c for c in data if c["category"] in KEEP_CATEGORIES]
    drop = [c for c in data if c["category"] in ARCHIVE_CATEGORIES]

    ARCHIVE.mkdir(exist_ok=True)
    moved = 0
    missing = 0
    for entry in drop:
        src = HOOKS / entry["file"]
        if not src.exists():
            missing += 1
            continue
        dst_dir = ARCHIVE / entry["category"]
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / src.name
        shutil.move(str(src), str(dst))
        moved += 1

    # Also move any now-empty category directories out of the way so
    # TransitionalManager.scan_for_new_clips() doesn't re-add ghosts.
    for cat in ARCHIVE_CATEGORIES:
        cat_dir = HOOKS / cat
        if cat_dir.exists() and not any(cat_dir.iterdir()):
            cat_dir.rmdir()

    INDEX.write_text(json.dumps(keep, indent=2))

    print(f"Kept {len(keep)} viral clips in live index.")
    print(f"Archived {moved} Pexels clips to {ARCHIVE}/")
    if missing:
        print(f"Note: {missing} index entries had no matching file on disk (skipped).")


if __name__ == "__main__":
    main()
