"""Tests for content_engine.pipeline — unified daily orchestrator."""
import pytest
from pathlib import Path
from unittest.mock import patch
from datetime import date
from content_engine.pipeline import (
    build_daily_clips,
    DailyPipelineConfig,
)
from content_engine.types import ClipFormat, TrendBrief, UnifiedWeights, TrackInfo


def test_config_defaults():
    # Locked slot allocation as of 2026-04-22:
    #   slot 0: SACRED_ARC (proven viral: bait hook + slow performance arc)
    #   slot 1: LONGFORM_TRAILER (short cut of longform + YouTube CTA)
    #   slot 2: PERFORMANCE_FAST_CUT (Anyma/ISOxo style, 0.4-0.7s cuts)
    config = DailyPipelineConfig()
    assert len(config.formats) == 3
    assert config.formats[0] == ClipFormat.SACRED_ARC
    assert config.formats[1] == ClipFormat.LONGFORM_TRAILER
    assert config.formats[2] == ClipFormat.PERFORMANCE_FAST_CUT


def test_config_durations():
    config = DailyPipelineConfig()
    assert config.durations[ClipFormat.TRANSITIONAL] == 22
    assert config.durations[ClipFormat.PERFORMANCE_FAST_CUT] == 22
    assert config.durations[ClipFormat.EMOTIONAL] == 7
    assert config.durations[ClipFormat.PERFORMANCE] == 28


# ── helper fixtures ────────────────────────────────────────────────────────────

def _make_track():
    return TrackInfo(
        title="jericho", file_path="", bpm=140, energy=0.7, danceability=0.7,
        valence=0.5, scripture_anchor="Joshua 6", spotify_id="", spotify_popularity=50,
        pool_weight=1.0, entered_pool=date.today().isoformat(),
    )


def _make_weights():
    return UnifiedWeights(
        hook_weights={}, visual_weights={}, format_weights={}, platform_weights={},
        transitional_category_weights={}, track_weights={},
        best_clip_length=22, best_platform="instagram", updated="2026-04-17",
    )


def _make_brief():
    return TrendBrief(
        date="2026-04-17", top_visual_formats=[], dominant_emotion="test",
        oversaturated="", hook_pattern_of_day="", contrarian_gap="test",
        trend_confidence=0.5,
    )


# ── story variant tests ────────────────────────────────────────────────────────

def test_story_path_equals_main_path(tmp_path):
    """story_path must equal path — no separate story re-render."""
    config = DailyPipelineConfig(
        formats=[ClipFormat.TRANSITIONAL],
        durations={ClipFormat.TRANSITIONAL: 22},
    )

    with patch("content_engine.renderer.render_transitional") as mock_render, \
         patch("content_engine.transitional_manager.TransitionalManager") as mock_tm, \
         patch("content_engine.generator.generate_hooks_for_format") as mock_hook, \
         patch("content_engine.generator.generate_caption") as mock_caption, \
         patch("content_engine.renderer.validate_output", return_value={"valid": True, "errors": []}):

        mock_tm.return_value.pick.return_value = {"file": "satisfying/123.mp4", "category": "satisfying"}
        mock_tm.return_value.full_path.return_value = Path("/fake/bait.mp4")
        mock_hook.return_value = {
            "hook": "test hook", "template_id": "t1",
            "mechanism": "save", "sub_mode": "COST", "exploration": False,
        }
        mock_caption.return_value = "test caption"
        mock_render.side_effect = lambda **kw: Path(kw["output_path"]).touch()

        clips = build_daily_clips(
            config, _make_brief(), _make_weights(), _make_track(), [30.0], str(tmp_path)
        )

    assert clips, "No clips returned"
    assert clips[0]["story_path"] == clips[0]["path"], (
        f"story_path != path: {clips[0]['story_path']!r} vs {clips[0]['path']!r}"
    )


def test_two_transitional_clips_get_distinct_output_paths(tmp_path):
    """Two TRANSITIONAL clips must write to different files — same path = same video = double-post."""
    config = DailyPipelineConfig(
        formats=[ClipFormat.TRANSITIONAL, ClipFormat.TRANSITIONAL],
        durations={ClipFormat.TRANSITIONAL: 22},
    )

    with patch("content_engine.renderer.render_transitional") as mock_render, \
         patch("content_engine.transitional_manager.TransitionalManager") as mock_tm, \
         patch("content_engine.generator.generate_hooks_for_format") as mock_hook, \
         patch("content_engine.generator.generate_caption") as mock_caption:

        mock_tm.return_value.pick.return_value = {"file": "satisfying/123.mp4", "category": "satisfying"}
        mock_tm.return_value.full_path.return_value = Path("/fake/bait.mp4")
        mock_hook.return_value = {
            "hook": "test hook", "template_id": "t1",
            "mechanism": "save", "sub_mode": "COST", "exploration": False,
        }
        mock_caption.return_value = "test caption"
        mock_render.side_effect = lambda **kw: Path(kw["output_path"]).touch()

        clips = build_daily_clips(
            config, _make_brief(), _make_weights(), _make_track(),
            peak_sections=[0.0, 0.0], output_dir=str(tmp_path),
        )

    assert len(clips) == 2
    paths = [c["path"] for c in clips]
    assert paths[0] != paths[1], (
        f"Both TRANSITIONAL clips wrote to the same path: {paths[0]!r}. "
        "This is the double-post bug — fix by including clip_idx in the filename."
    )


def test_performance_footage_found_in_subdirectory(tmp_path):
    """Videos nested inside a subdirectory of performances/ must be found."""
    from unittest.mock import patch as _patch
    from content_engine import pipeline as _pipeline

    perf_dir = tmp_path / "content" / "videos" / "performances" / "wetransfer_abc123"
    perf_dir.mkdir(parents=True)
    nested_mp4 = perf_dir / "LUC9222.MP4"
    nested_mp4.touch()

    (tmp_path / "content" / "videos" / "b-roll").mkdir(parents=True)
    (tmp_path / "content" / "videos" / "phone-footage").mkdir(parents=True)

    with _patch.object(_pipeline, "PROJECT_DIR", tmp_path):
        config = DailyPipelineConfig(
            formats=[ClipFormat.PERFORMANCE],
            durations={ClipFormat.PERFORMANCE: 28},
        )
        with patch("content_engine.renderer.render_performance") as mock_render, \
             patch("content_engine.generator.generate_hooks_for_format") as mock_hook, \
             patch("content_engine.generator.generate_caption") as mock_caption:

            mock_hook.return_value = {
                "hook": "test hook", "template_id": "t1",
                "mechanism": "save", "sub_mode": "BODY", "exploration": False,
            }
            mock_caption.return_value = "test caption"

            captured = {}
            # render_performance(segments, audio_path, audio_start, hook_text, platform, output_path, ...)
            def fake_render(content_segments, audio_path, audio_start, hook_text, platform, output_path, *a, **kw):
                captured["segments"] = content_segments
                Path(output_path).touch()
            mock_render.side_effect = fake_render

            (tmp_path / "output").mkdir(parents=True)
            build_daily_clips(
                config, _make_brief(), _make_weights(), _make_track(),
                peak_sections=[0.0], output_dir=str(tmp_path / "output"),
            )

    assert any("LUC9222.MP4" in s for s in captured.get("segments", [])), (
        "Performance footage nested in subdirectory was not found. "
        "iterdir() is one level deep — fix by using rglob()."
    )


def test_no_story_variant_files_generated(tmp_path):
    """build_daily_clips must not create any *_story.mp4 files."""
    config = DailyPipelineConfig(
        formats=[ClipFormat.PERFORMANCE],
        durations={ClipFormat.PERFORMANCE: 28},
    )

    with patch("content_engine.renderer.render_performance") as mock_render, \
         patch("content_engine.generator.generate_hooks_for_format") as mock_hook, \
         patch("content_engine.generator.generate_caption") as mock_caption, \
         patch("content_engine.renderer.validate_output", return_value={"valid": True, "errors": []}):

        mock_hook.return_value = {
            "hook": "test hook", "template_id": "t1",
            "mechanism": "dare", "sub_mode": "BODY", "exploration": False,
        }
        mock_caption.return_value = "test caption"
        mock_render.side_effect = lambda *a, **kw: Path(
            kw.get("output_path", str(tmp_path / "out.mp4"))
        ).touch()

        build_daily_clips(
            config, _make_brief(), _make_weights(), _make_track(), [30.0], str(tmp_path)
        )

    story_files = list(tmp_path.glob("*_story.mp4"))
    assert story_files == [], f"Unexpected story files: {story_files}"


def test_sacred_arc_uses_performance_footage_only(tmp_path):
    """SACRED_ARC segments must come exclusively from the performances/ directory."""
    from unittest.mock import patch as _patch
    from content_engine import pipeline as _pipeline

    perf_dir = tmp_path / "content" / "videos" / "performances" / "wetransfer_abc"
    perf_dir.mkdir(parents=True)
    (perf_dir / "DANCE1.MP4").touch()
    (perf_dir / "DANCE2.MP4").touch()

    (tmp_path / "content" / "videos" / "b-roll").mkdir(parents=True)
    (tmp_path / "content" / "videos" / "b-roll" / "NATURE1.mp4").touch()
    (tmp_path / "content" / "videos" / "phone-footage").mkdir(parents=True)

    with _patch.object(_pipeline, "PROJECT_DIR", tmp_path):
        config = DailyPipelineConfig(
            formats=[ClipFormat.SACRED_ARC],
            durations={ClipFormat.SACRED_ARC: 22},
        )
        with patch("content_engine.renderer.render_transitional") as mock_render, \
             patch("content_engine.transitional_manager.TransitionalManager") as mock_tm, \
             patch("content_engine.generator.generate_hooks_for_format") as mock_hook, \
             patch("content_engine.generator.generate_caption") as mock_caption:

            mock_tm.return_value.pick.return_value = {"file": "satisfying/123.mp4", "category": "satisfying"}
            mock_tm.return_value.full_path.return_value = Path("/fake/bait.mp4")
            mock_hook.return_value = {
                "hook": "test hook", "template_id": "t1",
                "mechanism": "save", "sub_mode": "COST", "exploration": False,
            }
            mock_caption.return_value = "test caption"

            captured = {}
            def fake_render(**kw):
                captured["segments"] = kw.get("content_segments", [])
                Path(kw["output_path"]).touch()
            mock_render.side_effect = fake_render

            build_daily_clips(
                config, _make_brief(), _make_weights(), _make_track(),
                peak_sections=[0.0], output_dir=str(tmp_path / "output"),
            )

    segments = captured.get("segments", [])
    assert segments, "No segments were passed to the renderer"
    for s in segments:
        assert "performances" in s, (
            f"SACRED_ARC used non-performance footage: {s!r}. "
            "Only content/videos/performances/ is allowed."
        )
    assert not any("b-roll" in s for s in segments), "b-roll leaked into SACRED_ARC segments"


def test_daily_config_default_uses_longform_trailer():
    """Default daily mix: [SACRED_ARC, LONGFORM_TRAILER, PERFORMANCE_FAST_CUT].

    Slot 1 was SACRED_ARC until 2026-04-22; replaced with LONGFORM_TRAILER so
    one daily clip drives traffic to the YouTube longform using the track's
    Kling morph clips as visuals + a YouTube URL appended to every caption.
    """
    config = DailyPipelineConfig()
    assert config.formats[0] == ClipFormat.SACRED_ARC
    assert config.formats[1] == ClipFormat.LONGFORM_TRAILER
    assert config.formats[2] == ClipFormat.PERFORMANCE_FAST_CUT


def test_config_durations_includes_longform_trailer():
    config = DailyPipelineConfig()
    assert ClipFormat.LONGFORM_TRAILER in config.durations
    assert config.durations[ClipFormat.LONGFORM_TRAILER] == 22


def test_get_latest_longform_url_returns_most_recent(tmp_path):
    from content_engine.pipeline import _get_latest_longform_url
    import json as _json
    jsonl = tmp_path / "youtube_longform.jsonl"
    jsonl.write_text(
        _json.dumps({"track_title": "Jericho", "youtube_url": "https://youtube.com/watch?v=OLD", "dry_run": False}) + "\n" +
        _json.dumps({"track_title": "Jericho", "youtube_url": "https://youtube.com/watch?v=NEW", "dry_run": False}) + "\n" +
        _json.dumps({"track_title": "Jericho", "youtube_url": "https://youtube.com/watch?v=DRY", "dry_run": True}) + "\n"
    )
    import unittest.mock as _mock
    with _mock.patch("content_engine.pipeline.PROJECT_DIR", tmp_path):
        (tmp_path / "data" / "youtube_longform").mkdir(parents=True)
        (tmp_path / "data" / "youtube_longform" / "youtube_longform.jsonl").write_text(jsonl.read_text())
        url = _get_latest_longform_url("jericho")
    assert url == "https://youtube.com/watch?v=NEW"


def test_get_latest_longform_url_skips_dry_runs(tmp_path):
    from content_engine.pipeline import _get_latest_longform_url
    import json as _json
    import unittest.mock as _mock
    jsonl_path = tmp_path / "data" / "youtube_longform" / "youtube_longform.jsonl"
    jsonl_path.parent.mkdir(parents=True)
    jsonl_path.write_text(
        _json.dumps({"track_title": "Selah", "youtube_url": "https://youtube.com/watch?v=DRYRUN", "dry_run": True}) + "\n"
    )
    with _mock.patch("content_engine.pipeline.PROJECT_DIR", tmp_path):
        url = _get_latest_longform_url("selah")
    assert url is None


def test_get_latest_longform_url_returns_none_when_missing(tmp_path):
    from content_engine.pipeline import _get_latest_longform_url
    import unittest.mock as _mock
    with _mock.patch("content_engine.pipeline.PROJECT_DIR", tmp_path):
        url = _get_latest_longform_url("jericho")
    assert url is None


def test_get_motion_clips_for_track_returns_track_specific(tmp_path):
    from content_engine.pipeline import _get_motion_clips_for_track
    import unittest.mock as _mock
    motion = tmp_path / "content" / "videos" / "holy-rave-motion"
    motion.mkdir(parents=True)
    (motion / "morph_rjm_selah_handpan_to_water_abc.mp4").touch()
    (motion / "morph_rjm_selah_oud_to_cave_def.mp4").touch()
    (motion / "morph_rjm_jericho_warrior_ghi.mp4").touch()
    with _mock.patch("content_engine.pipeline.PROJECT_DIR", tmp_path):
        clips = _get_motion_clips_for_track("selah")
    assert len(clips) == 2
    assert all("selah" in Path(c).name for c in clips)


def test_get_motion_clips_for_track_returns_empty_when_no_track_or_universal_match(tmp_path):
    """When only another track's named clips exist (no universals), return [].

    Pure track-slug clips (selah_*) must NOT be returned for a different track.
    """
    from content_engine.pipeline import _get_motion_clips_for_track
    import unittest.mock as _mock
    motion = tmp_path / "content" / "videos" / "holy-rave-motion"
    motion.mkdir(parents=True)
    (motion / "morph_rjm_selah_handpan_to_water.mp4").touch()
    (motion / "morph_rjm_selah_oud_to_cave.mp4").touch()
    with _mock.patch("content_engine.pipeline.PROJECT_DIR", tmp_path):
        clips = _get_motion_clips_for_track("halleluyah")
    assert clips == [], f"Expected [] but got {clips!r} — selah visuals must not appear under halleluyah caption"


def test_get_motion_clips_for_track_includes_universal_archetypes(tmp_path):
    """Universal clips (rjm_warrior/priestess/temple) are returned for any track.

    These archetypes appear in every track's Kling story and are visually
    appropriate for any track, including ones with no dedicated morph clips yet.
    """
    from content_engine.pipeline import _get_motion_clips_for_track
    import unittest.mock as _mock
    motion = tmp_path / "content" / "videos" / "holy-rave-motion"
    motion.mkdir(parents=True)
    (motion / "morph_rjm_selah_handpan.mp4").touch()
    (motion / "morph_rjm_warrior__to__rjm_priestess_abc.mp4").touch()
    (motion / "morph_rjm_priestess__to__rjm_temple_def.mp4").touch()
    (motion / "morph_rjm_temple__to__rjm_warrior_ghi.mp4").touch()
    with _mock.patch("content_engine.pipeline.PROJECT_DIR", tmp_path):
        # halleluyah has no specific clips — should still get universal archetypes
        clips = _get_motion_clips_for_track("halleluyah")
    assert len(clips) == 3, f"Expected 3 universal clips, got {len(clips)}: {clips}"
    assert not any("selah" in Path(c).name for c in clips), "Selah clip leaked into halleluyah"


def test_get_motion_clips_for_track_track_specific_before_universal(tmp_path):
    """Track-specific clips are returned before universal archetypes."""
    from content_engine.pipeline import _get_motion_clips_for_track
    import unittest.mock as _mock
    motion = tmp_path / "content" / "videos" / "holy-rave-motion"
    motion.mkdir(parents=True)
    (motion / "morph_rjm_jericho_wall_abc.mp4").touch()
    (motion / "morph_rjm_warrior__to__rjm_priestess_def.mp4").touch()
    with _mock.patch("content_engine.pipeline.PROJECT_DIR", tmp_path):
        clips = _get_motion_clips_for_track("jericho")
    assert len(clips) == 2
    # Track-specific must come first
    assert "jericho" in Path(clips[0]).name, f"Expected jericho clip first, got {clips[0]!r}"


def test_longform_trailer_appends_youtube_cta(tmp_path):
    """LONGFORM_TRAILER captions must include the YouTube URL for the track."""
    from unittest.mock import patch, MagicMock
    config = DailyPipelineConfig(
        formats=[ClipFormat.LONGFORM_TRAILER],
        durations={ClipFormat.LONGFORM_TRAILER: 22},
    )
    # Seed a motion clip so the segment pool is non-empty
    motion = tmp_path / "content" / "videos" / "holy-rave-motion"
    motion.mkdir(parents=True)
    clip = motion / "morph_rjm_jericho_warrior.mp4"
    clip.touch()

    captured_captions = {}

    def fake_render_transitional(**kwargs):
        pass

    with (
        patch("content_engine.pipeline.PROJECT_DIR", tmp_path),
        patch("content_engine.pipeline._get_latest_longform_url",
              return_value="https://youtube.com/watch?v=TESTID"),
        patch("content_engine.pipeline._get_motion_clips_for_track",
              return_value=[str(clip)]),
        patch("content_engine.transitional_manager.TransitionalManager") as MockTM,
        patch("content_engine.generator.generate_hooks_for_format",
              return_value={"hook": "test hook", "template_id": "t1",
                            "mechanism": "save", "sub_mode": "DEVOTION"}),
        patch("content_engine.generator.generate_caption",
              side_effect=lambda *a, **kw: "base caption"),
        patch("content_engine.renderer.render_transitional", fake_render_transitional),
    ):
        mock_tm = MagicMock()
        mock_tm.pick.return_value = {"file": "viral/test.mp4", "category": "viral"}
        mock_tm.full_path.return_value = str(tmp_path / "viral" / "test.mp4")
        MockTM.return_value = mock_tm
        (tmp_path / "viral").mkdir(parents=True)
        (tmp_path / "viral" / "test.mp4").touch()

        clips = build_daily_clips(
            config, _make_brief(), _make_weights(), _make_track(),
            peak_sections=[0.0], output_dir=str(tmp_path / "output"),
        )

    assert clips, "Expected at least one clip"
    caption_by_platform = clips[0].get("caption_by_platform", {})
    for platform, cap in caption_by_platform.items():
        assert "TESTID" in cap, (
            f"YouTube URL missing from {platform} caption: {cap!r}"
        )


def test_longform_trailer_no_url_renders_without_cta(tmp_path):
    """When no longform exists, LONGFORM_TRAILER renders cleanly without a CTA."""
    from unittest.mock import patch, MagicMock
    config = DailyPipelineConfig(
        formats=[ClipFormat.LONGFORM_TRAILER],
        durations={ClipFormat.LONGFORM_TRAILER: 22},
    )
    motion = tmp_path / "content" / "videos" / "holy-rave-motion"
    motion.mkdir(parents=True)
    clip = motion / "morph_rjm_jericho_warrior.mp4"
    clip.touch()

    with (
        patch("content_engine.pipeline.PROJECT_DIR", tmp_path),
        patch("content_engine.pipeline._get_latest_longform_url", return_value=None),
        patch("content_engine.pipeline._get_motion_clips_for_track", return_value=[str(clip)]),
        patch("content_engine.transitional_manager.TransitionalManager") as MockTM,
        patch("content_engine.generator.generate_hooks_for_format",
              return_value={"hook": "hook", "template_id": "t1",
                            "mechanism": "save", "sub_mode": "DEVOTION"}),
        patch("content_engine.generator.generate_caption", return_value="base caption"),
        patch("content_engine.renderer.render_transitional"),
    ):
        mock_tm = MagicMock()
        mock_tm.pick.return_value = {"file": "viral/test.mp4", "category": "viral"}
        mock_tm.full_path.return_value = str(tmp_path / "viral" / "test.mp4")
        MockTM.return_value = mock_tm
        (tmp_path / "viral").mkdir(parents=True)
        (tmp_path / "viral" / "test.mp4").touch()

        clips = build_daily_clips(
            config, _make_brief(), _make_weights(), _make_track(),
            peak_sections=[0.0], output_dir=str(tmp_path / "output"),
        )

    assert clips, "Expected a clip even without YouTube URL"
    assert clips[0]["format_type"] == "longform_trailer"
    for cap in clips[0].get("caption_by_platform", {}).values():
        assert "youtube.com/watch" not in cap


def test_longform_trailer_does_not_use_other_tracks_motion_clips(tmp_path):
    """LONGFORM_TRAILER must not use another track's Kling clips as visuals.

    Regression for: _get_motion_clips_for_track previously returned all clips
    when no track-specific match existed, causing selah morph visuals to appear
    under a jericho caption.
    """
    from unittest.mock import patch, MagicMock
    config = DailyPipelineConfig(
        formats=[ClipFormat.LONGFORM_TRAILER],
        durations={ClipFormat.LONGFORM_TRAILER: 22},
    )
    # Only selah clips in the pool — track is jericho
    motion = tmp_path / "content" / "videos" / "holy-rave-motion"
    motion.mkdir(parents=True)
    selah_clip = motion / "morph_rjm_selah_handpan.mp4"
    selah_clip.touch()
    # Perf pool as expected fallback
    perf = tmp_path / "content" / "videos" / "performances"
    perf.mkdir(parents=True)
    perf_clip = perf / "stage_jericho.mp4"
    perf_clip.touch()

    captured = {}

    def fake_render_transitional(**kwargs):
        captured["segments"] = kwargs.get("content_segments", [])

    jericho_track = _make_track()  # _make_track uses "jericho" title

    with (
        patch("content_engine.pipeline.PROJECT_DIR", tmp_path),
        patch("content_engine.pipeline._get_latest_longform_url", return_value=None),
        patch("content_engine.transitional_manager.TransitionalManager") as MockTM,
        patch("content_engine.generator.generate_hooks_for_format",
              return_value={"hook": "h", "template_id": "t1",
                            "mechanism": "save", "sub_mode": "DEVOTION"}),
        patch("content_engine.generator.generate_caption", return_value="cap"),
        patch("content_engine.renderer.render_transitional", fake_render_transitional),
    ):
        mock_tm = MagicMock()
        mock_tm.pick.return_value = {"file": "viral/test.mp4", "category": "viral"}
        mock_tm.full_path.return_value = str(tmp_path / "viral" / "test.mp4")
        MockTM.return_value = mock_tm
        (tmp_path / "viral").mkdir(parents=True)
        (tmp_path / "viral" / "test.mp4").touch()

        clips = build_daily_clips(
            config, _make_brief(), _make_weights(), jericho_track,
            peak_sections=[0.0], output_dir=str(tmp_path / "output"),
        )

    assert clips, "Expected clip to render"
    used = [s["path"] for s in clips[0].get("segments_used", [])]
    assert not any("selah" in p for p in used), (
        f"Selah morph clips leaked into jericho LONGFORM_TRAILER: {used}"
    )


def test_longform_trailer_visual_type_is_longform_motion(tmp_path):
    """Registry entry for LONGFORM_TRAILER must carry visual_type=longform_motion."""
    from unittest.mock import patch, MagicMock
    config = DailyPipelineConfig(
        formats=[ClipFormat.LONGFORM_TRAILER],
        durations={ClipFormat.LONGFORM_TRAILER: 22},
    )
    motion = tmp_path / "content" / "videos" / "holy-rave-motion"
    motion.mkdir(parents=True)
    (motion / "morph.mp4").touch()

    with (
        patch("content_engine.pipeline.PROJECT_DIR", tmp_path),
        patch("content_engine.pipeline._get_latest_longform_url", return_value=None),
        patch("content_engine.pipeline._get_motion_clips_for_track",
              return_value=[str(motion / "morph.mp4")]),
        patch("content_engine.transitional_manager.TransitionalManager") as MockTM,
        patch("content_engine.generator.generate_hooks_for_format",
              return_value={"hook": "h", "template_id": "t1",
                            "mechanism": "save", "sub_mode": "DEVOTION"}),
        patch("content_engine.generator.generate_caption", return_value="cap"),
        patch("content_engine.renderer.render_transitional"),
    ):
        mock_tm = MagicMock()
        mock_tm.pick.return_value = {"file": "viral/test.mp4", "category": "viral"}
        mock_tm.full_path.return_value = str(tmp_path / "viral" / "test.mp4")
        MockTM.return_value = mock_tm
        (tmp_path / "viral").mkdir(parents=True)
        (tmp_path / "viral" / "test.mp4").touch()

        clips = build_daily_clips(
            config, _make_brief(), _make_weights(), _make_track(),
            peak_sections=[0.0], output_dir=str(tmp_path / "output"),
        )

    assert clips[0]["visual_type"] == "longform_motion"


def test_bait_pick_biases_to_viral_category(tmp_path):
    """Bait pick must pass category_weights={viral: 1.0, others: 0.0} so only viral/ clips are used."""
    config = DailyPipelineConfig(
        formats=[ClipFormat.TRANSITIONAL],
        durations={ClipFormat.TRANSITIONAL: 22},
    )

    captured = {}

    with patch("content_engine.renderer.render_transitional"), \
         patch("content_engine.transitional_manager.TransitionalManager") as mock_tm, \
         patch("content_engine.generator.generate_hooks_for_format") as mock_hook, \
         patch("content_engine.generator.generate_caption") as mock_caption, \
         patch("content_engine.renderer.validate_output", return_value={"valid": True, "errors": []}):

        def capture_pick(*args, **kwargs):
            captured["weights"] = kwargs.get("category_weights")
            return {"file": "viral/Wrap-It-LOL.mp4", "category": "viral"}

        mock_tm.return_value.pick.side_effect = capture_pick
        mock_tm.return_value.full_path.return_value = Path("/fake/bait.mp4")
        mock_hook.return_value = {
            "hook": "test", "template_id": "t1",
            "mechanism": "save", "sub_mode": "COST", "exploration": False,
        }
        mock_caption.return_value = "test"

        build_daily_clips(
            config, _make_brief(), _make_weights(), _make_track(), [30.0], str(tmp_path)
        )

    w = captured.get("weights")
    assert w is not None, "pipeline did not pass category_weights to TransitionalManager.pick"
    assert w.get("viral", 0.0) > 0.0, f"viral weight should be >0, got {w}"
    non_viral = {k: v for k, v in w.items() if k != "viral"}
    assert all(v == 0.0 for v in non_viral.values()), (
        f"non-viral categories should be suppressed, got {non_viral}"
    )


# ── fast-cut format tests ─────────────────────────────────────────────────────

def test_compute_fast_cut_slices_returns_beat_aligned_cuts():
    """At 140 BPM the kick is ~0.43s — slice durations should land in [0.4, 0.7]s."""
    from content_engine.pipeline import compute_fast_cut_slices, FAST_CUT_MIN_S, FAST_CUT_MAX_S
    pool = ["/fake/clip1.mp4", "/fake/clip2.mp4", "/fake/clip3.mp4"]
    # Patch _safe_probe_duration so the helper doesn't try to ffprobe fake paths
    with patch("content_engine.pipeline._safe_probe_duration", return_value=20.0):
        slices = compute_fast_cut_slices(
            source_pool=pool, total_duration=18.0, bpm=140, segment_weights={}
        )
    # ≥ 4 fast cuts + 1 final hold
    assert len(slices) >= 5
    # All non-final slices land in the fast-cut window
    for src, s_start, s_end in slices[:-1]:
        dur = s_end - s_start
        assert FAST_CUT_MIN_S - 0.01 <= dur <= FAST_CUT_MAX_S + 0.01, (
            f"slice duration {dur:.2f}s outside fast-cut window"
        )
    # Final slice held longer (the "hold" for the drop to breathe)
    *_, hold_start, hold_end = slices[-1]
    hold_dur = hold_end - hold_start
    assert hold_dur >= 2.5, f"final hold {hold_dur:.2f}s too short"


def test_compute_fast_cut_slices_empty_pool_returns_empty():
    from content_engine.pipeline import compute_fast_cut_slices
    assert compute_fast_cut_slices(source_pool=[], total_duration=20.0, bpm=140) == []


def test_compute_fast_cut_slices_biases_to_high_weight_segments():
    """Segments with higher weight should be picked more often. Run many trials."""
    from content_engine.pipeline import compute_fast_cut_slices
    pool = ["/fake/winner.mp4", "/fake/loser.mp4"]
    weights = {"winner.mp4": 5.0, "loser.mp4": 0.1}
    counts = {"winner.mp4": 0, "loser.mp4": 0}
    with patch("content_engine.pipeline._safe_probe_duration", return_value=15.0):
        for _ in range(40):
            slices = compute_fast_cut_slices(
                source_pool=pool, total_duration=18.0, bpm=140, segment_weights=weights,
            )
            for src, *_ in slices:
                key = src.split("/")[-1]
                counts[key] = counts.get(key, 0) + 1
    assert counts["winner.mp4"] > counts["loser.mp4"] * 3, (
        f"weighted bias not reflected in picks: {counts}"
    )


def test_segments_used_populated_in_clip_meta(tmp_path):
    """Every clip's meta carries segments_used so learning loop can score footage."""
    config = DailyPipelineConfig(
        formats=[ClipFormat.SACRED_ARC],
        durations={ClipFormat.SACRED_ARC: 22},
    )
    captured_clips: list = []

    with patch("content_engine.transitional_manager.TransitionalManager") as mock_tm, \
         patch("content_engine.generator.generate_hooks_for_format") as mock_hook, \
         patch("content_engine.generator.generate_caption") as mock_caption, \
         patch("content_engine.renderer.render_transitional") as mock_render, \
         patch("pathlib.Path.rglob") as mock_rglob, \
         patch("pathlib.Path.is_file", return_value=True), \
         patch("pathlib.Path.exists", return_value=True):

        mock_tm.return_value.pick.return_value = {
            "file": "viral/test.mp4", "category": "viral"
        }
        mock_tm.return_value.full_path.return_value = Path("/fake/bait.mp4")
        mock_hook.return_value = {
            "hook": "test", "template_id": "t1",
            "mechanism": "save", "sub_mode": "COST", "exploration": False,
        }
        mock_caption.return_value = "test"
        # Fake source pool — return paths under performances/
        fake_paths = [
            Path("/proj/content/videos/performances/clip_a.mp4"),
            Path("/proj/content/videos/performances/clip_b.mp4"),
            Path("/proj/content/videos/performances/clip_c.mp4"),
        ]
        mock_rglob.return_value = fake_paths
        mock_render.return_value = str(tmp_path / "out.mp4")

        clips = build_daily_clips(
            config, _make_brief(), _make_weights(), _make_track(), [30.0], str(tmp_path)
        )
        captured_clips.extend(clips)

    assert captured_clips, "no clips returned"
    for clip in captured_clips:
        assert "segments_used" in clip, f"clip missing segments_used: {clip.get('format_type')}"
        assert clip["segments_used"], "segments_used should not be empty"
        for seg in clip["segments_used"]:
            assert "file" in seg and "start" in seg and "end" in seg, (
                f"segments_used entry missing keys: {seg}"
            )
