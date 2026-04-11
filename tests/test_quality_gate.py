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


# ── Visual check tests ─────────────────────────────────────────────────────────

class TestVisualChecks:
    def test_passes_on_high_score(self):
        from quality_gate import _score_frame

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"score": 4, "reason": "Good dark energy"}')]

        with patch("quality_gate.anthropic.Anthropic") as MockClient:
            MockClient.return_value.messages.create.return_value = mock_response
            passed, reason = _score_frame("fake_b64", angle="energy")

        assert passed is True
        assert reason == ""

    def test_rejects_on_low_score(self):
        from quality_gate import _score_frame

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"score": 1, "reason": "Black frame, nothing visible"}')]

        with patch("quality_gate.anthropic.Anthropic") as MockClient:
            MockClient.return_value.messages.create.return_value = mock_response
            passed, reason = _score_frame("fake_b64", angle="energy")

        assert passed is False
        assert "1/5" in reason
        assert "Black frame" in reason

    def test_passes_through_on_unparseable_response(self):
        from quality_gate import _score_frame

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="not valid json")]

        with patch("quality_gate.anthropic.Anthropic") as MockClient:
            MockClient.return_value.messages.create.return_value = mock_response
            passed, reason = _score_frame("fake_b64", angle="energy")

        assert passed is True

    def test_visual_error_passes_through(self, tmp_path):
        """If frame extraction throws, the clip is not blocked."""
        from quality_gate import _check_visual
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"x" * 2_000_000)

        with patch("quality_gate.subprocess.run", side_effect=RuntimeError("ffmpeg not found")):
            passed, reason = _check_visual(str(clip), angle="energy")

        assert passed is True


# ── Full check_clip integration tests ─────────────────────────────────────────

class TestCheckClip:
    def test_skips_visual_when_technical_fails(self, tmp_path):
        """Visual check must NOT run if technical check already failed."""
        from quality_gate import check_clip
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"x" * 2_000_000)

        with patch("quality_gate._run_ffprobe", return_value=_ffprobe_result(width=640, height=480)):
            with patch("quality_gate._check_visual") as mock_visual:
                with patch("quality_gate._log_result"):
                    passed, reason = check_clip(str(clip), expected_duration=9)

        assert passed is False
        mock_visual.assert_not_called()

    def test_runs_visual_after_technical_passes(self, tmp_path):
        """Visual check runs when technical passes."""
        from quality_gate import check_clip
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"x" * 2_000_000)

        with patch("quality_gate._run_ffprobe", return_value=_ffprobe_result()):
            with patch("quality_gate._check_visual", return_value=(True, "")) as mock_visual:
                with patch("quality_gate._log_result"):
                    passed, reason = check_clip(str(clip), expected_duration=9)

        mock_visual.assert_called_once()
        assert passed is True

    def test_logs_result_on_pass(self, tmp_path, monkeypatch):
        from quality_gate import check_clip
        import quality_gate
        log_path = tmp_path / "quality_log.json"
        monkeypatch.setattr(quality_gate, "LOG_PATH", log_path)

        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"x" * 2_000_000)

        with patch("quality_gate._run_ffprobe", return_value=_ffprobe_result()):
            with patch("quality_gate._check_visual", return_value=(True, "")):
                check_clip(str(clip), expected_duration=9, angle="energy")

        log = json.loads(log_path.read_text())
        assert len(log) == 1
        assert log[0]["passed"] is True
        assert log[0]["angle"] == "energy"

    def test_logs_result_on_fail(self, tmp_path, monkeypatch):
        from quality_gate import check_clip
        import quality_gate
        log_path = tmp_path / "quality_log.json"
        monkeypatch.setattr(quality_gate, "LOG_PATH", log_path)

        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"x" * 100)  # too small

        with patch("quality_gate._run_ffprobe", return_value=_ffprobe_result()):
            check_clip(str(clip), expected_duration=9)

        log = json.loads(log_path.read_text())
        assert log[0]["passed"] is False
        assert log[0]["reason"] != ""
