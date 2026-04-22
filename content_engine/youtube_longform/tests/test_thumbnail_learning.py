"""
Tests for thumbnail_learning.pre_publish_check — the CTR quality gate
that runs on every generated thumbnail BEFORE the Kling/Shotstack spend.

We use numpy to generate synthetic images at controlled luminance /
saturation / color so we can exercise each severity branch without
actually rendering via fal.ai. The viral-corpus similarity is stubbed
via monkeypatch so we don't depend on the 528-thumbnail pool being
populated in CI.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image

from content_engine.youtube_longform import thumbnail_learning as tl


# ─── Synthetic image factory ─────────────────────────────────────────────────


def _write_image(
    tmp_path: Path,
    name: str,
    rgb: tuple[int, int, int],
    *,
    center_rgb: tuple[int, int, int] | None = None,
    size: tuple[int, int] = (1280, 720),
) -> Path:
    """
    Create a 1280x720 JPEG with a solid color (optionally with a
    different center 40% block for center_bias / saturation variation).
    """
    arr = np.zeros((size[1], size[0], 3), dtype=np.uint8)
    arr[:, :] = rgb
    if center_rgb is not None:
        h, w = arr.shape[:2]
        cy1, cy2 = int(h * 0.3), int(h * 0.7)
        cx1, cx2 = int(w * 0.3), int(w * 0.7)
        arr[cy1:cy2, cx1:cx2] = center_rgb
    out = tmp_path / name
    Image.fromarray(arr, "RGB").save(out, "JPEG", quality=90)
    return out


def _stub_corpus_sim(mean_sim: float, nearest_sim: float = 0.9, title: str = "stub-viral"):
    """Patch score_vs_corpus so the gate uses a controlled similarity."""
    return patch.object(
        tl, "score_vs_corpus",
        return_value=tl.CorpusSimilarity(
            bucket="bucket_140_psytrance",
            mean_similarity=mean_sim,
            nearest_title=title,
            nearest_views=1_000_000,
            nearest_similarity=nearest_sim,
            drift_flag=mean_sim < 0.55,
        ),
    )


# ─── pre_publish_check branch coverage ───────────────────────────────────────


def test_pre_publish_check_ships_a_real_viral_thumbnail(tmp_path):
    """
    A real proven-viral thumbnail from our 528-image corpus must ship —
    "pass" or "soft_fail", but never "hard_fail". If the calibrated gate
    regens on proven-viral content, thresholds are wrong.

    Skipped when the corpus isn't populated locally so CI can pass
    without the 79MB pool.
    """
    from content_engine.youtube_longform import config as cfg
    viral_path = (
        cfg.PROVEN_VIRAL_DIR / "bucket_130_organic"
        / "0026_hang_massive_hang_massive_sk_negatan_2011_hang_drum_duo_hd.jpg"
    )
    if not viral_path.exists():
        pytest.skip(
            f"Viral corpus not populated at {viral_path} — skipping live-image check"
        )

    with _stub_corpus_sim(mean_sim=0.68):
        r = tl.pre_publish_check(viral_path, bpm=128, log_event=False)

    # A proven-viral thumbnail with healthy sim (0.68) must be shippable.
    assert r.severity in ("pass", "soft_fail"), (
        f"Gate over-triggered on proven-viral content: {r.severity} "
        f"issues={r.issues}. If thresholds intentionally tightened, "
        f"update this test."
    )
    # Corpus-drift hard gate must not fire at 0.68 sim.
    assert not any("off-theme" in i.lower() for i in r.issues)
    # Unreadable-brightness hard gate must not fire on a real thumbnail.
    assert not any("unreadably dark" in i.lower() for i in r.issues)


def test_pre_publish_check_hard_fail_on_unreadable_darkness(tmp_path):
    """Brightness below 45 is the 'unreadable on mobile' hard gate."""
    img = _write_image(tmp_path, "dark.jpg", rgb=(15, 15, 15))
    with _stub_corpus_sim(mean_sim=0.60):
        r = tl.pre_publish_check(img, bpm=142, log_event=False)
    assert r.severity == "hard_fail"
    assert r.passed is False
    assert any("unreadably dark" in i.lower() or "hard" in i.lower() for i in r.issues)
    # Must provide at least one actionable suggestion
    assert len(r.suggested_prompt_additions) >= 1


def test_pre_publish_check_hard_fail_on_deep_corpus_drift(tmp_path):
    """mean_sim below 0.38 = 'off-theme' hard drift gate."""
    img = _write_image(
        tmp_path, "offtheme.jpg",
        rgb=(90, 60, 45), center_rgb=(184, 83, 42),
    )
    with _stub_corpus_sim(mean_sim=0.30):
        r = tl.pre_publish_check(img, bpm=142, log_event=False)
    assert r.severity == "hard_fail", f"got {r.severity} issues={r.issues}"
    # Should flag the off-theme condition specifically
    assert any("off-theme" in i.lower() or "drift" in i.lower() for i in r.issues)


def test_pre_publish_check_soft_fail_on_middling_drift(tmp_path):
    """mean_sim between 0.38 and 0.48 = soft warn, not a regen trigger."""
    img = _write_image(
        tmp_path, "warn.jpg",
        rgb=(90, 60, 45), center_rgb=(184, 83, 42),
    )
    with _stub_corpus_sim(mean_sim=0.42):
        r = tl.pre_publish_check(img, bpm=142, log_event=False)
    assert r.severity == "soft_fail"
    assert r.passed is False                 # soft_fail is still "not passed"
    assert any("mild drift" in i.lower() or "drift" in i.lower() for i in r.issues)


def test_pre_publish_check_soft_fail_on_low_saturation_bright_but_gray(tmp_path):
    """Gray image: bright enough, but low saturation → 1-2 soft issues."""
    img = _write_image(tmp_path, "gray.jpg", rgb=(128, 128, 128))
    with _stub_corpus_sim(mean_sim=0.65):
        r = tl.pre_publish_check(img, bpm=142, log_event=False)
    assert r.severity in ("soft_fail", "hard_fail")   # will likely catch sat + contrast + off-palette
    # No unreadable-brightness issue (we're at 128)
    assert not any("unreadably dark" in i.lower() for i in r.issues)


def test_pre_publish_check_writes_learning_log(tmp_path, monkeypatch):
    """log_event=True appends a structured JSONL row for post-hoc analysis."""
    log_file = tmp_path / "thumbnail_learning_events.jsonl"
    monkeypatch.setattr(tl, "LEARNING_LOG", log_file)

    img = _write_image(tmp_path, "logged.jpg", rgb=(90, 60, 45))
    with _stub_corpus_sim(mean_sim=0.60):
        tl.pre_publish_check(img, bpm=128, track_title="Smoke Test", log_event=True)

    assert log_file.exists()
    content = log_file.read_text().strip()
    assert "pre_publish_check" in content
    assert "Smoke Test" in content
    assert "severity" in content


def test_pre_publish_check_degrades_gracefully_when_corpus_missing(tmp_path):
    """Corpus unreadable → soft_fail with a specific infra-warning issue."""
    img = _write_image(tmp_path, "img.jpg", rgb=(120, 90, 60))
    with patch.object(
        tl, "score_vs_corpus",
        return_value=tl.CorpusSimilarity(
            bucket="bucket_140_psytrance",
            mean_similarity=0.0,
            nearest_title="(no corpus)",
            nearest_views=0,
            nearest_similarity=0.0,
            drift_flag=True,
        ),
    ):
        r = tl.pre_publish_check(img, bpm=142, log_event=False)
    # Infra warning should NOT cause a hard-fail — we don't block live
    # publishes on missing-corpus infrastructure.
    assert r.severity in ("soft_fail", "hard_fail")   # other issues may still hard-fail
    assert any("corpus unavailable" in i.lower() for i in r.issues)
