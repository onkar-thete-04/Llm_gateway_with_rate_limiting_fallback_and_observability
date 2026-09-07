"""Retry with exponential backoff and retryable-error classification.

``retry_with_backoff`` invokes an async callable up to ``attempts`` times,
retrying only errors classified as retryable (timeouts, rate limits, 5xx).
Non-retryable errors (auth failures, content policy) raise immediately.
"""
from __future__ import annotations

import asyncio
import random
from typing import Awaitable, Callable, TypeVar

import httpx

from app.providers.base import ProviderError

T = TypeVar("T")


def is_retryable(exc: Exception) -> bool:
    """True for transient failures worth retrying / failing over on."""
    if isinstance(exc, ProviderError):
        return exc.retryable
    return isinstance(exc, httpx.TimeoutException)


def backoff_delay(attempt: int, base: float = 0.5, factor: float = 2.0) -> float:
    """Exponential backoff with jitter: base * factor**attempt + jitter."""
    jitter = random.uniform(0.0, base * 0.5)
    return base * (factor ** attempt) + jitter


async def retry_with_backoff(
    fn: Callable[[], Awaitable[T]],
    attempts: int = 3,
    base: float = 0.5,
    factor: float = 2.0,
    on_retry: Callable[[int, Exception], Awaitable[None]] | None = None,
) -> T:
    """Call ``fn``, retrying retryable errors with exponential backoff.

    Raises the last retryable error once ``attempts`` are exhausted, or any
    non-retryable error immediately. ``on_retry`` fires before each retry.
    """
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001 - classification below
            last = exc
            if not is_retryable(exc) or attempt == attempts - 1:
                raise
            if on_retry is not None:
                await on_retry(attempt + 1, exc)
            await asyncio.sleep(backoff_delay(attempt, base, factor))
    assert last is not None
    raise last
