"""Retry wrapper for Slack API calls with exponential backoff.

Handles rate limits (HTTP 429) and transient failures.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, TypeVar

logger = logging.getLogger("feature_factory.slack_retry")

T = TypeVar("T")

MAX_RETRIES = 3
BASE_DELAY = 1.0  # seconds
MAX_DELAY = 10.0  # seconds


def slack_retry(
    fn: Callable[..., T],
    *args: Any,
    max_retries: int = MAX_RETRIES,
    **kwargs: Any,
) -> T:
    """Call a Slack SDK method with retry on rate limit or transient error.

    Respects Retry-After header from Slack 429 responses.
    Retries on ConnectionError, TimeoutError, and Slack rate limit errors.
    Raises the final exception if all retries fail.
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            exc_str = str(exc).lower()

            # Check if this is a rate limit (429) or server error (5xx)
            retry_after = None
            if hasattr(exc, "response"):
                response = exc.response
                if hasattr(response, "status_code") and response.status_code == 429:
                    retry_after = float(
                        getattr(response, "headers", {}).get("Retry-After", BASE_DELAY)
                    )
                elif hasattr(response, "status_code") and response.status_code >= 500:
                    pass  # Retry on 5xx
                else:
                    raise  # Don't retry client errors (4xx except 429)
            elif "ratelimit" in exc_str or "rate_limit" in exc_str:
                retry_after = BASE_DELAY
            elif not isinstance(exc, (ConnectionError, TimeoutError, OSError)):
                if "SlackApiError" not in type(exc).__name__:
                    raise  # Don't retry unknown errors

            if attempt >= max_retries:
                logger.warning(
                    "slack_retry_exhausted fn=%s attempts=%d error=%s",
                    fn.__name__ if hasattr(fn, "__name__") else str(fn),
                    attempt + 1,
                    exc,
                )
                raise

            delay = retry_after or min(BASE_DELAY * (2 ** attempt), MAX_DELAY)
            logger.info(
                "slack_retry_attempt fn=%s attempt=%d/%d delay=%.1fs error=%s",
                fn.__name__ if hasattr(fn, "__name__") else str(fn),
                attempt + 1,
                max_retries,
                delay,
                exc,
            )
            time.sleep(delay)

    raise last_exc  # type: ignore[misc]  # Should never reach here
