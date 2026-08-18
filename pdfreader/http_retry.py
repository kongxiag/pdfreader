# -*- coding: utf-8 -*-
"""Shared retry decisions for OpenAI-compatible HTTP clients."""
from __future__ import annotations

import random
import time
import urllib.error
from email.utils import parsedate_to_datetime
from typing import Callable, TypeVar

T = TypeVar("T")
_TRANSIENT_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


def is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in _TRANSIENT_STATUS
    return isinstance(exc, (urllib.error.URLError, TimeoutError))


def retry_delay(exc: BaseException, attempt: int) -> float:
    if isinstance(exc, urllib.error.HTTPError):
        value = exc.headers.get("Retry-After") if exc.headers else None
        if value:
            try:
                return max(0.0, min(float(value), 60.0))
            except ValueError:
                try:
                    target = parsedate_to_datetime(value).timestamp()
                    return max(0.0, min(target - time.time(), 60.0))
                except (TypeError, ValueError, OverflowError):
                    pass
    return min(2 ** attempt + random.uniform(0.0, 0.5), 30.0)


def run_with_retries(
    operation: Callable[[], T],
    *,
    max_attempts: int,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    attempts = max(1, max_attempts)
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception as exc:  # noqa: BLE001
            if attempt >= attempts or not is_retryable(exc):
                raise
            sleep(retry_delay(exc, attempt))
    raise AssertionError("unreachable")
