"""Tests for content_engine.pipeline — unified daily orchestrator."""
import pytest
from content_engine.pipeline import (
    build_daily_clips,
    DailyPipelineConfig,
)
from content_engine.types import ClipFormat


def test_config_defaults():
    config = DailyPipelineConfig()
    assert len(config.formats) == 3
    assert config.formats[0] == ClipFormat.TRANSITIONAL
    assert config.formats[1] == ClipFormat.EMOTIONAL
    assert config.formats[2] == ClipFormat.PERFORMANCE


def test_config_durations():
    config = DailyPipelineConfig()
    assert config.durations[ClipFormat.TRANSITIONAL] == 22
    assert config.durations[ClipFormat.EMOTIONAL] == 7
    assert config.durations[ClipFormat.PERFORMANCE] == 28
