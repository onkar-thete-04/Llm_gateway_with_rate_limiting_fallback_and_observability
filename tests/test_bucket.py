import fakeredis.aioredis
import pytest

from app.limits.bucket import TokenBucket


@pytest.fixture
async def bucket():
    redis = fakeredis.aioredis.FakeRedis()
    return TokenBucket(redis)


async def test_take_consumes_tokens(bucket):
    result = await bucket.take("k", capacity=5.0, refill_rate=5.0 / 60.0, requested=1.0)
    assert result.allowed is True
    assert result.remaining == 4.0


async def test_take_denies_when_exhausted(bucket):
    for _ in range(5):
        await bucket.take("k", capacity=5.0, refill_rate=5.0 / 60.0, requested=1.0)
    result = await bucket.take("k", capacity=5.0, refill_rate=5.0 / 60.0, requested=1.0)
    assert result.allowed is False
    assert result.retry_after > 0


async def test_take_zero_capacity_allows(bucket):
    result = await bucket.take("k", capacity=0.0, refill_rate=0.0, requested=1.0)
    assert result.allowed is True
