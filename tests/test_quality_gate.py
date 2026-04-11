import pytest
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'outreach_agent'))


def _ffprobe_result(width=1080, height=1920, duration=9.0, codec="h264", has_audio=True):
    """Helper: build a fake ffprobe JSON result."""
    streams = [
        {
            "codec_type": "video",
            "codec_name": codec,
            "width": width,
            "height": height,
            "r_frame_rate": "30/1",
        }
    ]
    if has_audio:
        streams.append({"codec_type": "audio", "codec_name": "aac"})
    return {
        "streams": streams,
        "format": {"duration": str(duration), "size": str(5_000_000)},
    }


class TestTechnicalChecks:
    def test_passes_valid_clip(self, tmp_path):
        from quality_gate import _check_technical
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"x" * 2_000_000)

        with patch("quality_gate._run_ffprobe", return_value=_ffprobe_result()):
            passed, reason = _check_technical(str(clip), expected_duration=9)

        assert passed is True
        assert reason == ""

    def test_fails_wrong_resolution(self, tmp_path):
        from quality_gate import _check_technical
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"x" * 2_000_000)

        with patch("quality_gate._run_ffprobe", return_value=_ffprobe_result(width=1920, height=1080)):
            passed, reason = _check_technical(str(clip), expected_duration=9)

        assert passed is False
        assert "resolution" in reason.lower()

    def test_fails_wrong_duration(self, tmp_path):
        from quality_gate import _check_technical
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"x" * 2_000_000)

        with patch("quality_gate._run_ffprobe", return_value=_ffprobe_result(duration=20.0)):
            passed, reason = _check_technical(str(clip), expected_duration=9)

        assert passed is False
        assert "duration" in reason.lower()

    def test_fails_missing_audio(self, tmp_path):
        from quality_gate import _check_technical
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"x" * 2_000_000)

        with patch("quality_gate._run_ffprobe", return_value=_ffprobe_result(has_audio=False)):
            passed, reason = _check_technical(str(clip), expected_duration=9)

        assert passed is False
        assert "audio" in reason.lower()

    def test_fails_file_too_small(self, tmp_path):
        from quality_gate import _check_technical
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"x" * 100)

        with patch("quality_gate._run_ffprobe", return_value=_ffprobe_result()):
            passed, reason = _check_technical(str(clip), expected_duration=9)

        assert passed is False
        assert "size" in reason.lower()

    def test_fails_when_ffprobe_errors(self, tmp_path):
        from quality_gate import _check_technical
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"x" * 2_000_000)

        with patch("quality_gate._run_ffprobe", side_effect=RuntimeError("ffprobe not found")):
            passed, reason = _check_technical(str(clip), expected_duration=9)

        assert passed is False
        assert reason != ""
