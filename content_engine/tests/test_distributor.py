# content_engine/tests/test_distributor.py
import pytest
from content_engine.distributor import (
    DISTRIBUTION_TARGETS,
    POST_SCHEDULE,
    _scheduled_at_utc,
    CircuitBreaker,
)


def test_distribution_targets():
    expected = {"instagram", "youtube", "facebook", "tiktok", "instagram_story", "facebook_story"}
    assert set(DISTRIBUTION_TARGETS) == expected


def test_post_schedule_has_6_targets():
    for target in DISTRIBUTION_TARGETS:
        assert target in POST_SCHEDULE


def test_scheduled_at_utc():
    result = _scheduled_at_utc("instagram", 0)
    assert "T" in result  # ISO format


def test_circuit_breaker_init():
    cb = CircuitBreaker()
    assert not cb.is_open("instagram")


def test_circuit_breaker_trips_after_3():
    cb = CircuitBreaker(threshold=3)
    cb.record_failure("instagram")
    cb.record_failure("instagram")
    assert not cb.is_open("instagram")
    cb.record_failure("instagram")
    assert cb.is_open("instagram")


def test_circuit_breaker_reset():
    cb = CircuitBreaker(threshold=3)
    for _ in range(3):
        cb.record_failure("instagram")
    assert cb.is_open("instagram")
    cb.reset("instagram")
    assert not cb.is_open("instagram")


def test_circuit_breaker_success_resets():
    cb = CircuitBreaker(threshold=3)
    cb.record_failure("instagram")
    cb.record_failure("instagram")
    cb.record_success("instagram")
    assert not cb.is_open("instagram")
