import httpx
import pytest

from app.providers.base import ProviderError
from app.resilience.retry import backoff_delay, is_retryable, retry_with_backoff


def test_is_retryable_classification():
    assert is_retryable(ProviderError("timeout", retryable=True))
    assert is_retryable(httpx.TimeoutException("slow"))
    assert not is_retryable(ProviderError("auth", retryable=False))
    assert not is_retryable(ValueError("boom"))


async def test_succeeds_within_attempts(monkeypatch):
    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ProviderError("flaky", retryable=True)
        return "ok"

    result = await retry_with_backoff(fn, attempts=3)
    assert result == "ok"
    assert calls["n"] == 3


async def test_non_retryable_raises_immediately():
    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        raise ProviderError("bad key", status_code=401, retryable=False)

    with pytest.raises(ProviderError):
        await retry_with_backoff(fn, attempts=3)
    assert calls["n"] == 1


async def test_exhausts_attempts_then_raises(monkeypatch):
    monkeypatch.setattr("app.resilience.retry.asyncio.sleep", _no_sleep)
    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        raise ProviderError("always down", retryable=True)

    with pytest.raises(ProviderError):
        await retry_with_backoff(fn, attempts=3)
    assert calls["n"] == 3


async def test_on_retry_fires_per_retry(monkeypatch):
    monkeypatch.setattr("app.resilience.retry.asyncio.sleep", _no_sleep)
    fired = []

    async def fn():
        raise ProviderError("flaky", retryable=True)

    async def hook(attempt, exc):
        fired.append(attempt)

    with pytest.raises(ProviderError):
        await retry_with_backoff(fn, attempts=3, on_retry=hook)
    assert fired == [1, 2]


def test_backoff_grows():
    assert backoff_delay(0) < backoff_delay(1) < backoff_delay(2)


async def _no_sleep(_):
    return None
