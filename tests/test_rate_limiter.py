import fakeredis.aioredis
import pytest

from app.api.schemas import UnifiedChatRequest, UnifiedMessage
from app.limits.bucket import TokenBucket
from app.limits.rate_limiter import RateLimitError, RateLimiter, estimate_input_tokens


@pytest.fixture
async def limiter():
    redis = fakeredis.aioredis.FakeRedis()
    return RateLimiter(TokenBucket(redis))


def make_request(text="hello world"):
    return UnifiedChatRequest(
        model="gpt-4o",
        messages=[UnifiedMessage(role="user", content=text)],
        priority="realtime",
    )


def test_estimate_input_tokens():
    req = make_request("x" * 400)
    assert estimate_input_tokens(req) == 100


async def test_rate_limiter_allows_within_limit(limiter):
    await limiter.check("acme", "realtime", tier_rpm=10.0, tier_tpm=1000.0, input_tokens=10)


async def test_rate_limiter_rejects_over_rpm(limiter):
    for _ in range(5):
        await limiter.check("acme", "realtime", tier_rpm=5.0, tier_tpm=0.0, input_tokens=1)
    with pytest.raises(RateLimitError) as exc:
        await limiter.check("acme", "realtime", tier_rpm=5.0, tier_tpm=0.0, input_tokens=1)
    assert exc.value.retry_after > 0


async def test_rate_limiter_rejects_over_tpm(limiter):
    with pytest.raises(RateLimitError):
        await limiter.check("acme", "realtime", tier_rpm=0.0, tier_tpm=50.0, input_tokens=500)
