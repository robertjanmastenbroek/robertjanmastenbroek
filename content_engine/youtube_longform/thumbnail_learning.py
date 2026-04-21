"""
thumbnail_learning.py — Self-improving thumbnails via CTR feedback.

Closes the loop between what we generate and what actually drives clicks.
Pipeline:

  1. Pull YouTube Analytics CTR/APV per Holy Rave video (daily).
  2. Score each thumbnail's composition (brightness, saturation, contrast,
     face-centric anchoring) using numpy/PIL — pure-Python, no ffmpeg.
  3. Score visual similarity to the proven-viral corpus in the matching
     BPM bucket via RGB-histogram distance + palette matching.
  4. Diagnose underperformers with heuristic rules. Produce a weekly
     markdown report with per-video CTR, diagnostic, and a rewritten
     prompt that addresses the diagnosis.
  5. Generate candidate v2 thumbnails on user approval. Swap via
     thumbnails.set. Log outcomes for next-week comparison.

No CLIP dependency — RGB histogram + palette distance captures ~80% of
the signal at zero extra cost. Upgrade path is swappable.

Safety: no auto-swap. YouTube penalizes channels that flap thumbnails
more than a few times per video. Weekly analyzer produces a report; user
approves specific regenerations before we call the swap API.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests

from content_engine.audio_engine import TRACK_BPMS
from content_engine.youtube_longform import config as cfg
from content_engine.youtube_longform import registry

logger = logging.getLogger(__name__)

METRICS_FILE = cfg.REGISTRY_DIR / "thumbnail_metrics.jsonl"
REPORT_FILE  = cfg.REGISTRY_DIR / "thumbnail_learning_report.md"
LEARNING_LOG = cfg.REGISTRY_DIR / "thumbnail_learning_events.jsonl"

# Thresholds — calibrated conservatively. YouTube music average CTR is
# ~4-6%; strong channels hit 8-12%; viral music content peaks 15%+.
CTR_UNDERPERFORM_THRESHOLD = 0.04   # flag if CTR < 4%
IMPRESSIONS_SIGNIFICANCE   = 200    # ignore videos with fewer impressions
APV_UNDERPERFORM_THRESHOLD = 0.30   # flag if average view % < 30% (thumbnail or
                                    # track fit problem, both are learnings)

REGEN_BUDGET_WEEKLY_USD = 0.75      # cap weekly thumbnail regen spend


# ─── Data types ──────────────────────────────────────────────────────────────

@dataclass
class ThumbnailMetrics:
    """One row of per-video CTR/APV data."""
    timestamp:        str
    video_id:         str
    track_title:      str
    ctr:              float          # impressions_click_through_rate
    impressions:      int
    apv:              float          # average view percentage (0-1)
    avd_seconds:      float          # average view duration
    views:            int
    watch_time_hours: float
    window_days:      int            # how many days the metrics cover


@dataclass
class CompositionScore:
    """Numpy/PIL analysis of a thumbnail image."""
    brightness_mean:  float          # 0-255
    brightness_std:   float
    saturation_mean:  float          # 0-1
    contrast:         float          # stddev of luminance
    warmth:           float          # (R+G) - B, normalized
    center_bias:      float          # how much of frame is dominated by center
                                     # (high = strong hero subject, low = spread out)
    dominant_rgb:     tuple[int, int, int]
    palette_on_brand: bool           # True if dominant RGB is in locked tokens


@dataclass
class CorpusSimilarity:
    """Score vs proven-viral corpus for the matching BPM bucket."""
    bucket:             str
    mean_similarity:    float        # 0-1, mean against top-30 viral in bucket
    nearest_title:      str          # which viral thumbnail matches best
    nearest_views:      int
    nearest_similarity: float
    drift_flag:         bool         # True if mean_similarity < 0.55


@dataclass
class Diagnosis:
    """Assembled diagnosis for one underperforming video."""
    video_id:           str
    track_title:        str
    ctr:                float
    issues:             list[str]
    suggested_prompt_additions: list[str]   # strings to append to thumbnail prompt


# ─── Analytics pull ──────────────────────────────────────────────────────────

def _refresh_access_token() -> str:
    """Mint a fresh access token from HOLYRAVE_REFRESH_TOKEN."""
    rt  = cfg.YT_REFRESH_TOKEN
    cid = cfg.YT_CLIENT_ID
    cs  = cfg.YT_CLIENT_SECRET
    if not (rt and cid and cs):
        raise RuntimeError(
            "Missing YT OAuth creds (HOLYRAVE_REFRESH_TOKEN + CLIENT_ID + SECRET)"
        )
    r = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "refresh_token": rt, "client_id": cid, "client_secret": cs,
            "grant_type": "refresh_token",
        },
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def pull_metrics(days: int = 28) -> list[ThumbnailMetrics]:
    """
    Pull per-video CTR/APV for every Holy Rave video in the registry.
    Appends rows to thumbnail_metrics.jsonl. Returns the rows written.

    Needs yt-analytics.readonly scope (included in setup_youtube_oauth.py).
    """
    cfg.ensure_workspace()
    METRICS_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Collect video_ids from our own registry
    video_rows: list[dict] = []
    if registry.REGISTRY_FILE.exists():
        with open(registry.REGISTRY_FILE) as f:
            for line in f:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("youtube_id") and not row.get("error") and not row.get("dry_run"):
                    video_rows.append(row)

    if not video_rows:
        logger.info("No Holy Rave videos in registry yet — skip metrics pull")
        return []

    token = _refresh_access_token()
    end_date   = datetime.now(timezone.utc).date().isoformat()
    start_date = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()

    results: list[ThumbnailMetrics] = []
    for row in video_rows:
        vid = row["youtube_id"]
        r = requests.get(
            "https://youtubeanalytics.googleapis.com/v2/reports",
            params={
                "ids":        "channel==MINE",
                "startDate":  start_date,
                "endDate":    end_date,
                "metrics":    "views,estimatedMinutesWatched,averageViewDuration,"
                              "averageViewPercentage,impressions,"
                              "impressionsClickThroughRate",
                "filters":    f"video=={vid}",
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        if r.status_code != 200:
            logger.warning("YT Analytics %s: %d %s", vid, r.status_code, r.text[:200])
            continue
        data = r.json()
        rows = data.get("rows") or []
        if not rows:
            logger.info("No analytics yet for %s (probably too new)", vid)
            continue
        cols = data.get("columnHeaders", [])
        col_names = [c.get("name") for c in cols]
        values = rows[0]
        # Defensive column lookup — order can vary
        def v(name, default=0):
            try:
                return values[col_names.index(name)]
            except (ValueError, IndexError):
                return default

        ctr = float(v("impressionsClickThroughRate", 0.0)) / 100.0  # returned as %
        m = ThumbnailMetrics(
            timestamp=datetime.now(timezone.utc).isoformat(),
            video_id=vid,
            track_title=row.get("track_title", ""),
            ctr=ctr,
            impressions=int(v("impressions", 0)),
            apv=float(v("averageViewPercentage", 0.0)) / 100.0,
            avd_seconds=float(v("averageViewDuration", 0.0)),
            views=int(v("views", 0)),
            watch_time_hours=float(v("estimatedMinutesWatched", 0.0)) / 60.0,
            window_days=days,
        )
        results.append(m)
        with open(METRICS_FILE, "a") as f:
            f.write(json.dumps({**m.__dict__}) + "\n")
        logger.info(
            "metrics %s: CTR %.2f%% impressions %d views %d APV %.1f%%",
            m.track_title, m.ctr * 100, m.impressions, m.views, m.apv * 100,
        )
    return results


# ─── Composition scoring (pure numpy/PIL, no ffmpeg) ─────────────────────────

def score_composition(image_path: Path) -> CompositionScore:
    """
    Compute composition metrics using PIL + numpy. No face-detection yet —
    we approximate "subject centeredness" by measuring saturation/contrast
    concentration in the central 40% of the frame.
    """
    import numpy as np
    from PIL import Image

    img = Image.open(image_path).convert("RGB")
    arr = np.array(img, dtype=np.float32)  # H x W x 3

    # Brightness (luminance)
    luminance = 0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]
    brightness_mean = float(luminance.mean())
    brightness_std  = float(luminance.std())

    # Saturation (HSV)
    r, g, b = arr[..., 0] / 255, arr[..., 1] / 255, arr[..., 2] / 255
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    sat = np.where(mx > 0, (mx - mn) / np.where(mx > 0, mx, 1), 0.0)
    saturation_mean = float(sat.mean())

    # Contrast = stddev of luminance (how much range across image)
    contrast = brightness_std

    # Warmth: (R+G) - B normalized
    warmth = float(((arr[..., 0] + arr[..., 1]) / 2 - arr[..., 2]).mean() / 255)

    # Center bias: how much more saturated the center 40% is vs outer 60%
    h, w = sat.shape
    cy1, cy2 = int(h * 0.3), int(h * 0.7)
    cx1, cx2 = int(w * 0.3), int(w * 0.7)
    center_sat = sat[cy1:cy2, cx1:cx2].mean()
    outer_mask = np.ones_like(sat)
    outer_mask[cy1:cy2, cx1:cx2] = 0
    outer_sat = (sat * outer_mask).sum() / (outer_mask.sum() + 1e-9)
    center_bias = float(center_sat / (outer_sat + 1e-9))

    # Dominant RGB via coarse 3x3x3 histogram mode
    rb = (arr[..., 0] // 86).astype(int)
    gb = (arr[..., 1] // 86).astype(int)
    bb = (arr[..., 2] // 86).astype(int)
    bins = rb * 9 + gb * 3 + bb
    bin_counts = np.bincount(bins.flatten(), minlength=27)
    top_bin = int(np.argmax(bin_counts))
    dom_r = (top_bin // 9) * 86 + 43
    dom_g = ((top_bin // 3) % 3) * 86 + 43
    dom_b = (top_bin % 3) * 86 + 43
    dominant_rgb = (int(dom_r), int(dom_g), int(dom_b))

    # Locked palette check — Dark #0a0a0a, liturgical gold #d4af37,
    # terracotta #b8532a, indigo-night #1a2a4a, ochre #c8883a
    palette_tokens = [
        (10, 10, 10), (212, 175, 55), (184, 83, 42),
        (26, 42, 74), (200, 136, 58),
    ]
    def rgb_dist(a, b):
        return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5
    palette_on_brand = min(rgb_dist(dominant_rgb, p) for p in palette_tokens) < 80

    return CompositionScore(
        brightness_mean=brightness_mean,
        brightness_std=brightness_std,
        saturation_mean=saturation_mean,
        contrast=contrast,
        warmth=warmth,
        center_bias=center_bias,
        dominant_rgb=dominant_rgb,
        palette_on_brand=palette_on_brand,
    )


# ─── Corpus similarity (RGB histogram distance) ──────────────────────────────

def _bucket_for_bpm(bpm: int) -> str:
    """Map BPM to viral-corpus bucket name (matches content/images/proven_viral/)."""
    return "bucket_140_psytrance" if bpm >= 139 else "bucket_130_organic"


def _load_viral_manifest() -> list[dict]:
    manifest_path = cfg.PROVEN_VIRAL_DIR / "manifest.json"
    if not manifest_path.exists():
        return []
    with open(manifest_path) as f:
        return json.load(f)


def _rgb_histogram(image_path: Path, bins: int = 8):
    """Return a flattened RGB histogram as a normalized numpy vector."""
    import numpy as np
    from PIL import Image
    img = Image.open(image_path).convert("RGB").resize((128, 72))
    arr = np.array(img)
    r_hist, _ = np.histogram(arr[..., 0], bins=bins, range=(0, 256))
    g_hist, _ = np.histogram(arr[..., 1], bins=bins, range=(0, 256))
    b_hist, _ = np.histogram(arr[..., 2], bins=bins, range=(0, 256))
    v = np.concatenate([r_hist, g_hist, b_hist]).astype(float)
    n = v.sum()
    return v / n if n > 0 else v


def score_vs_corpus(image_path: Path, bpm: int, top_k: int = 30) -> CorpusSimilarity:
    """
    Compare our thumbnail against the top-k highest-viewed entries in the
    matching BPM bucket via RGB histogram cosine similarity.
    """
    import numpy as np

    bucket = _bucket_for_bpm(bpm)
    manifest = _load_viral_manifest()
    # Bucket name in manifest uses "130_organic" / "140_psytrance" (no prefix)
    bucket_short = bucket.replace("bucket_", "")
    bucket_entries = [m for m in manifest if m.get("bucket") == bucket_short]
    if not bucket_entries:
        return CorpusSimilarity(
            bucket=bucket, mean_similarity=0.0,
            nearest_title="(no corpus)", nearest_views=0,
            nearest_similarity=0.0, drift_flag=True,
        )

    # Top k by view count
    bucket_entries.sort(key=lambda e: e.get("view_count_estimate", 0), reverse=True)
    top = bucket_entries[:top_k]

    our_vec = _rgb_histogram(image_path)

    def cosine(a, b):
        num = float(np.dot(a, b))
        den = float((np.linalg.norm(a) * np.linalg.norm(b)) + 1e-9)
        return num / den

    sims: list[tuple[float, dict]] = []
    for entry in top:
        ref_path = cfg.PROVEN_VIRAL_DIR / bucket / entry["filename"]
        if not ref_path.exists():
            continue
        try:
            ref_vec = _rgb_histogram(ref_path)
            sims.append((cosine(our_vec, ref_vec), entry))
        except Exception as e:
            logger.debug("skip %s: %s", ref_path.name, e)

    if not sims:
        return CorpusSimilarity(
            bucket=bucket, mean_similarity=0.0,
            nearest_title="(corpus unreadable)", nearest_views=0,
            nearest_similarity=0.0, drift_flag=True,
        )

    sims.sort(key=lambda s: s[0], reverse=True)
    mean_sim = sum(s[0] for s in sims) / len(sims)
    nearest_sim, nearest_entry = sims[0]

    return CorpusSimilarity(
        bucket=bucket,
        mean_similarity=mean_sim,
        nearest_title=nearest_entry.get("title", "(unknown)"),
        nearest_views=int(nearest_entry.get("view_count_estimate", 0)),
        nearest_similarity=nearest_sim,
        drift_flag=mean_sim < 0.55,
    )


# ─── Diagnosis + prompt rewriting ────────────────────────────────────────────

# Corpus-derived baselines. These are hand-set from visual inspection of
# the 528-thumbnail pool; refine once we have 10+ Holy Rave publishes with
# CTR data to do a proper regression.
VIRAL_BASELINE = {
    "brightness_min":  70,
    "saturation_min":  0.38,
    "contrast_min":    45,
    "center_bias_min": 1.1,      # center noticeably more saturated than outer
}


def diagnose(
    video_id:      str,
    track_title:   str,
    metrics:       ThumbnailMetrics,
    composition:   CompositionScore,
    corpus_sim:    CorpusSimilarity,
) -> Diagnosis:
    """Assemble a diagnosis + prompt-improvement suggestions for a video."""
    issues: list[str] = []
    suggestions: list[str] = []

    if metrics.impressions < IMPRESSIONS_SIGNIFICANCE:
        issues.append(
            f"Only {metrics.impressions} impressions — below significance "
            f"({IMPRESSIONS_SIGNIFICANCE}); CTR not reliable yet."
        )

    if metrics.ctr < CTR_UNDERPERFORM_THRESHOLD and metrics.impressions >= IMPRESSIONS_SIGNIFICANCE:
        issues.append(
            f"CTR {metrics.ctr*100:.2f}% below {CTR_UNDERPERFORM_THRESHOLD*100:.0f}% threshold"
        )

    if metrics.apv < APV_UNDERPERFORM_THRESHOLD:
        issues.append(
            f"APV {metrics.apv*100:.1f}% below {APV_UNDERPERFORM_THRESHOLD*100:.0f}% — "
            f"viewers bouncing fast; thumbnail may mis-promise or track mis-fits audience"
        )

    if composition.brightness_mean < VIRAL_BASELINE["brightness_min"]:
        issues.append(
            f"Too dark (brightness {composition.brightness_mean:.0f} < baseline "
            f"{VIRAL_BASELINE['brightness_min']})"
        )
        suggestions.append(
            "brighter golden-hour light on the subject's face, warm amber rim-lighting, "
            "overall exposure raised"
        )

    if composition.saturation_mean < VIRAL_BASELINE["saturation_min"]:
        issues.append(
            f"Low saturation ({composition.saturation_mean:.2f} < baseline "
            f"{VIRAL_BASELINE['saturation_min']:.2f})"
        )
        suggestions.append(
            "deeper saturated tones, rich terracotta and gold and indigo, "
            "cinematic grade with color punch"
        )

    if composition.contrast < VIRAL_BASELINE["contrast_min"]:
        issues.append(
            f"Flat contrast ({composition.contrast:.0f} < baseline "
            f"{VIRAL_BASELINE['contrast_min']})"
        )
        suggestions.append(
            "dramatic side-lighting with deep obsidian-black shadow on one side, "
            "single golden key light carving the face"
        )

    if composition.center_bias < VIRAL_BASELINE["center_bias_min"]:
        issues.append(
            f"Weak center focus (center-bias {composition.center_bias:.2f} < "
            f"{VIRAL_BASELINE['center_bias_min']:.2f}) — hero subject not dominating"
        )
        suggestions.append(
            "tighter crop so the subject's face occupies 70 percent of the frame, "
            "shallow depth of field with background fully blurred"
        )

    if corpus_sim.drift_flag:
        issues.append(
            f"Visual drift from viral corpus (mean sim {corpus_sim.mean_similarity:.2f} "
            f"< 0.55). Nearest viral match: \"{corpus_sim.nearest_title}\" "
            f"({corpus_sim.nearest_views:,} views, sim {corpus_sim.nearest_similarity:.2f})"
        )
        # No prompt suggestion — drift usually means we need to swap the
        # composition concept entirely, which is better done manually.

    if not composition.palette_on_brand:
        issues.append(
            f"Off-palette — dominant color {composition.dominant_rgb} not "
            f"in locked token set"
        )
        suggestions.append(
            "dominant palette of terracotta #b8532a and indigo-night #1a2a4a "
            "with liturgical gold #d4af37 accent only"
        )

    return Diagnosis(
        video_id=video_id,
        track_title=track_title,
        ctr=metrics.ctr,
        issues=issues,
        suggested_prompt_additions=suggestions,
    )


# ─── Weekly report ───────────────────────────────────────────────────────────

def weekly_report() -> Path:
    """
    Pull latest metrics, score every thumbnail, produce a markdown report
    at data/youtube_longform/thumbnail_learning_report.md.
    """
    cfg.ensure_workspace()

    try:
        metrics_list = pull_metrics(days=28)
    except Exception as e:
        logger.warning("Metrics pull failed: %s — using last cached", e)
        metrics_list = []

    # Combine with cached history (latest per video_id)
    latest_by_vid: dict[str, ThumbnailMetrics] = {}
    if METRICS_FILE.exists():
        with open(METRICS_FILE) as f:
            for line in f:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                m = ThumbnailMetrics(**row)
                latest_by_vid[m.video_id] = m
    for m in metrics_list:
        latest_by_vid[m.video_id] = m

    if not latest_by_vid:
        REPORT_FILE.write_text(
            "# Holy Rave — thumbnail learning report\n\n"
            f"Generated: {datetime.now(timezone.utc).isoformat()}\n\n"
            "No metrics yet. Publish at least one video and let it accumulate "
            "~200 impressions before running this again.\n"
        )
        return REPORT_FILE

    # For each video, locate thumbnail + BPM via registry
    reg_by_vid: dict[str, dict] = {}
    with open(registry.REGISTRY_FILE) as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("youtube_id"):
                reg_by_vid[row["youtube_id"]] = row

    diagnoses: list[Diagnosis] = []
    for vid, m in latest_by_vid.items():
        reg = reg_by_vid.get(vid)
        if not reg:
            continue
        thumb_path = Path(reg["hero_image"]) if reg.get("hero_image") else None
        if not thumb_path or not thumb_path.exists():
            logger.info("skip %s: thumbnail file not available for analysis", vid)
            continue
        bpm = TRACK_BPMS.get(m.track_title.lower().strip(), 130)
        try:
            comp = score_composition(thumb_path)
            corp = score_vs_corpus(thumb_path, bpm)
            d = diagnose(vid, m.track_title, m, comp, corp)
            diagnoses.append(d)
        except Exception as e:
            logger.warning("diagnose failed for %s: %s", vid, e)

    # Rank by CTR ascending (worst first — these need attention)
    diagnoses.sort(key=lambda d: d.ctr)

    md = [
        "# Holy Rave — thumbnail learning report",
        f"Generated: **{datetime.now(timezone.utc).isoformat()}**",
        f"Videos analyzed: **{len(diagnoses)}**",
        "",
        "## Ranked by CTR (worst first — focus here)",
        "",
        "| # | Track | CTR | Impr | APV | Views | Issues |",
        "|---|-------|----:|-----:|----:|------:|--------|",
    ]
    for i, d in enumerate(diagnoses, 1):
        m = latest_by_vid[d.video_id]
        md.append(
            f"| {i} | **{d.track_title}** | {d.ctr*100:.2f}% | {m.impressions:,} | "
            f"{m.apv*100:.1f}% | {m.views:,} | {len(d.issues)} |"
        )

    md.append("\n## Per-video diagnostics")
    for d in diagnoses:
        m = latest_by_vid[d.video_id]
        md.append(f"\n### [{d.track_title}](https://youtube.com/watch?v={d.video_id})")
        md.append(
            f"CTR **{d.ctr*100:.2f}%** · {m.impressions:,} impressions · "
            f"APV {m.apv*100:.1f}% · {m.views:,} views"
        )
        if not d.issues:
            md.append("\n*No issues flagged — thumbnail performing within baselines.*")
            continue
        md.append("\n**Issues:**")
        for issue in d.issues:
            md.append(f"- {issue}")
        if d.suggested_prompt_additions:
            md.append("\n**Suggested prompt additions for v2 thumbnail:**")
            for s in d.suggested_prompt_additions:
                md.append(f"- {s}")

    md.append("")
    md.append("## What to do next")
    md.append(
        "Review the \"worst first\" ranking above. For each track flagged "
        "with actionable prompt suggestions, decide whether to regenerate "
        "a v2 thumbnail. When ready:"
    )
    md.append("")
    md.append("```bash")
    md.append("# regenerate thumbnail for a specific video (~$0.08 per call)")
    md.append("python3 -m content_engine.youtube_longform.thumbnail_learning regen <video_id>")
    md.append("```")
    md.append("")
    md.append(
        "YouTube penalizes thumbnail flapping — limit swaps to ≤2 per month "
        "per video. First data point lands after ~200 impressions (typically "
        "24-72 hours post-publish)."
    )

    REPORT_FILE.write_text("\n".join(md) + "\n")
    logger.info("Wrote report: %s", REPORT_FILE)
    return REPORT_FILE


# ─── Candidate regeneration (future — gated) ─────────────────────────────────

def regenerate_thumbnail(video_id: str, dry_run: bool = True) -> Optional[Path]:
    """
    Regenerate a thumbnail for an underperforming video using the learning
    loop's prompt suggestions. Not auto-invoked — call explicitly from the
    CLI after reviewing the weekly report.

    dry_run=True prints the improved prompt without spending on Flux.
    """
    # Load registry entry
    reg = None
    with open(registry.REGISTRY_FILE) as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("youtube_id") == video_id:
                reg = row
                break
    if not reg:
        raise RuntimeError(f"video_id {video_id} not in registry")

    track_title = reg["track_title"]
    bpm = TRACK_BPMS.get(track_title.lower().strip(), 130)

    # Pull latest metrics
    metrics_list = pull_metrics(days=28)
    m = next((x for x in metrics_list if x.video_id == video_id), None)
    if not m:
        raise RuntimeError(f"no metrics yet for {video_id}; let it run ~48h first")

    # Score + diagnose
    thumb_path = Path(reg["hero_image"])
    comp = score_composition(thumb_path)
    corp = score_vs_corpus(thumb_path, bpm)
    d = diagnose(video_id, track_title, m, comp, corp)

    if not d.suggested_prompt_additions:
        logger.info("No prompt suggestions — thumbnail within baselines")
        return None

    # Load the original prompt from the motion story (the thumbnail_keyframe
    # if present, else the first in-chain keyframe)
    from content_engine.youtube_longform import motion as motion_mod
    story = motion_mod.story_for_track(track_title)
    base_kf = story.thumbnail_keyframe or story.keyframes[0]

    additions = ". ".join(d.suggested_prompt_additions)
    improved_prompt = base_kf.still_prompt.rstrip(", ") + ". " + additions + "."

    logger.info("Improved prompt:\n%s", improved_prompt)
    if dry_run:
        return None

    # Generate via Flux 2 Pro /edit using the matching viral-corpus references
    from content_engine.youtube_longform.image_gen import _generate_one, _download
    from content_engine.youtube_longform import reference_pool
    from content_engine.youtube_longform.render import upload_image_for_render

    refs = reference_pool.pick_references(
        "tribal_psytrance" if bpm >= 139 else "organic_house"
    )
    reference_urls = []
    for r in refs:
        try:
            reference_urls.append(upload_image_for_render(r, public_id=f"ref_{r.stem}"))
        except Exception as e:
            logger.warning("ref upload skip: %s", e)

    import hashlib
    digest = hashlib.sha256(improved_prompt.encode()).hexdigest()[:8]
    out_path = cfg.IMAGE_DIR / f"thumb_v2_{video_id}_{digest}.jpg"
    url = _generate_one(
        prompt=improved_prompt,
        negative_prompt="",
        width=cfg.HERO_WIDTH,
        height=cfg.HERO_HEIGHT,
        seed=None,
        reference_urls=reference_urls or None,
    )
    _download(url, out_path)

    # Log the event for outcome tracking
    with open(LEARNING_LOG, "a") as f:
        f.write(json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "regenerated_thumbnail",
            "video_id": video_id,
            "track_title": track_title,
            "reason": " | ".join(d.issues),
            "new_path": str(out_path),
            "prompt_additions": d.suggested_prompt_additions,
        }) + "\n")

    logger.info("Regenerated thumbnail saved: %s", out_path)
    logger.info("To apply: call uploader.set_thumbnail(video_id, out_path)")
    return out_path


# ─── CLI entry point ─────────────────────────────────────────────────────────

def main():
    import argparse
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("pull", help="Pull latest metrics from YT Analytics")
    sub.add_parser("report", help="Generate weekly markdown report")

    regen = sub.add_parser("regen", help="Regenerate a thumbnail based on diagnosis")
    regen.add_argument("video_id")
    regen.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()
    if args.cmd == "pull":
        rows = pull_metrics()
        print(f"Pulled metrics for {len(rows)} video(s)")
    elif args.cmd == "report":
        p = weekly_report()
        print(f"Report: {p}")
    elif args.cmd == "regen":
        regenerate_thumbnail(args.video_id, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
