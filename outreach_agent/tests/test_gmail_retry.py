"""Regression: gmail_client retries transient failures with exponential backoff."""
from unittest.mock import MagicMock, patch
import pytest


class _FakeResp:
    def __init__(self, status):
        self.status = status
        self.reason = f"status-{status}"


def _http_error(status: int):
    from googleapiclient.errors import HttpError
    return HttpError(resp=_FakeResp(status), content=b'{"error":{"message":"transient"}}')


def test_is_transient_http_error_classifies_5xx_and_429():
    import gmail_client as gc
    assert gc._is_transient_http_error(_http_error(500)) is True
    assert gc._is_transient_http_error(_http_error(503)) is True
    assert gc._is_transient_http_error(_http_error(429)) is True


def test_is_transient_http_error_rejects_4xx_auth():
    import gmail_client as gc
    assert gc._is_transient_http_error(_http_error(401)) is False
    assert gc._is_transient_http_error(_http_error(403)) is False
    assert gc._is_transient_http_error(_http_error(404)) is False


def test_is_transient_http_error_accepts_connection_errors():
    import gmail_client as gc
    assert gc._is_transient_http_error(ConnectionError("reset")) is True
    assert gc._is_transient_http_error(TimeoutError("slow")) is True


def test_execute_with_retry_recovers_on_second_attempt(monkeypatch):
    import gmail_client as gc
    monkeypatch.setattr(gc.time, "sleep", lambda s: None)

    request = MagicMock()
    request.execute.side_effect = [_http_error(503), {"id": "ok"}]

    result = gc._execute_with_retry(request, label="test")
    assert result == {"id": "ok"}
    assert request.execute.call_count == 2


def test_execute_with_retry_raises_after_max_attempts(monkeypatch):
    import gmail_client as gc
    monkeypatch.setattr(gc.time, "sleep", lambda s: None)

    request = MagicMock()
    request.execute.side_effect = _http_error(500)

    with pytest.raises(Exception):
        gc._execute_with_retry(request, label="test")
    assert request.execute.call_count == gc.SEND_RETRY_MAX_ATTEMPTS


def test_execute_with_retry_does_not_retry_non_transient(monkeypatch):
    import gmail_client as gc
    monkeypatch.setattr(gc.time, "sleep", lambda s: None)

    request = MagicMock()
    request.execute.side_effect = _http_error(403)

    with pytest.raises(Exception):
        gc._execute_with_retry(request, label="test")
    assert request.execute.call_count == 1
