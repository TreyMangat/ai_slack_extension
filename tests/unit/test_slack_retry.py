"""Tests for Slack retry logic."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.slack_retry import slack_retry


def test_succeeds_first_try():
    fn = MagicMock(return_value="ok")
    result = slack_retry(fn, "arg1", key="val")
    assert result == "ok"
    fn.assert_called_once_with("arg1", key="val")


def test_retries_on_connection_error():
    fn = MagicMock(side_effect=[ConnectionError("timeout"), "ok"])
    with patch("app.services.slack_retry.time.sleep"):
        result = slack_retry(fn, max_retries=2)
    assert result == "ok"
    assert fn.call_count == 2


def test_retries_on_timeout_error():
    fn = MagicMock(side_effect=[TimeoutError("timed out"), "ok"])
    with patch("app.services.slack_retry.time.sleep"):
        result = slack_retry(fn, max_retries=2)
    assert result == "ok"
    assert fn.call_count == 2


def test_retries_on_os_error():
    fn = MagicMock(side_effect=[OSError("network unreachable"), "ok"])
    with patch("app.services.slack_retry.time.sleep"):
        result = slack_retry(fn, max_retries=2)
    assert result == "ok"
    assert fn.call_count == 2


def test_retries_on_rate_limit_string():
    exc = Exception("ratelimited")
    fn = MagicMock(side_effect=[exc, "ok"])
    with patch("app.services.slack_retry.time.sleep"):
        result = slack_retry(fn, max_retries=2)
    assert result == "ok"


def test_retries_on_rate_limit_429():
    response = MagicMock(status_code=429, headers={"Retry-After": "2"})
    exc = Exception("rate limited")
    exc.response = response
    fn = MagicMock(side_effect=[exc, "ok"])
    with patch("app.services.slack_retry.time.sleep") as mock_sleep:
        result = slack_retry(fn, max_retries=2)
    assert result == "ok"
    mock_sleep.assert_called_once_with(2.0)


def test_retries_on_5xx():
    response = MagicMock(status_code=503, headers={})
    exc = Exception("service unavailable")
    exc.response = response
    fn = MagicMock(side_effect=[exc, "ok"])
    with patch("app.services.slack_retry.time.sleep"):
        result = slack_retry(fn, max_retries=2)
    assert result == "ok"


def test_does_not_retry_4xx():
    response = MagicMock(status_code=400, headers={})
    exc = Exception("bad request")
    exc.response = response
    fn = MagicMock(side_effect=exc)
    with pytest.raises(Exception, match="bad request"):
        slack_retry(fn, max_retries=3)
    fn.assert_called_once()


def test_raises_after_max_retries():
    fn = MagicMock(side_effect=ConnectionError("down"))
    with patch("app.services.slack_retry.time.sleep"):
        with pytest.raises(ConnectionError):
            slack_retry(fn, max_retries=2)
    assert fn.call_count == 3  # initial + 2 retries


def test_does_not_retry_value_error():
    fn = MagicMock(side_effect=ValueError("bad input"))
    with pytest.raises(ValueError):
        slack_retry(fn, max_retries=3)
    fn.assert_called_once()


def test_does_not_retry_key_error():
    fn = MagicMock(side_effect=KeyError("missing"))
    with pytest.raises(KeyError):
        slack_retry(fn, max_retries=3)
    fn.assert_called_once()


def test_exponential_backoff_timing():
    fn = MagicMock(side_effect=[ConnectionError(), ConnectionError(), "ok"])
    with patch("app.services.slack_retry.time.sleep") as mock_sleep:
        slack_retry(fn, max_retries=3)
    assert mock_sleep.call_count == 2
    delays = [call.args[0] for call in mock_sleep.call_args_list]
    assert delays[0] <= delays[1]  # exponential growth
