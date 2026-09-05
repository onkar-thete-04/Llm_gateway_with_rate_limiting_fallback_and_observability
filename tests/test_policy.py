import fakeredis.aioredis
import pytest

from app.admin.overrides import Overrides
from app.api.schemas import UnifiedChatRequest, UnifiedMessage
from app.config.schema import BudgetConfig, RateLimitConfig, TeamConfig
from app.limits.budget import BudgetChecker
from app.limits.bucket import TokenBucket
from app.limits.policy import RateLimitPolicy
from app.limits.pricing import PricingTable
from app.limits.rate_limiter import RateLimitError, RateLimiter


def team(**updates):
    defaults = dict(
        name="acme",
        api_key="k",
        allowed_models=["gpt-4o"],
        allowed_providers=["openai"],
        rate_limit=RateLimitConfig(requests_per_minute=100, tokens_per_minute=1000),
        budget=BudgetConfig(amount_usd=100.0, window="monthly", warn_at=0.8),
    )
    defaults.update(updates)
    return TeamConfig(**defaults)


def request(priority="realtime"):
    return UnifiedChatRequest(
        model="gpt-4o",
        messages=[UnifiedMessage(role="user", content="hello world")],
        priority=priority,
    )


@pytest.fixture
async def policy():
    redis = fakeredis.aioredis.FakeRedis()
    pricing = PricingTable.load("pricing.yaml")
    overrides = Overrides(redis)
    return RateLimitPolicy(
        RateLimiter(TokenBucket(redis)),
        BudgetChecker(redis, pricing),
        pricing,
        overrides=overrides,
        alert_manager=None,
    )


def test_tier_split_defaults():
    assert RateLimitPolicy._tier_limits(team(), "realtime", 100.0, 1000.0) == (70.0, 700.0)
    assert RateLimitPolicy._tier_limits(team(), "batch", 100.0, 1000.0) == (30.0, 300.0)


def test_tier_split_override():
    t = team(rate_limit=RateLimitConfig(requests_per_minute=100, tokens_per_minute=1000, tiers={"realtime": {"requests_per_minute": 50}}))
    assert RateLimitPolicy._tier_limits(t, "realtime", 100.0, 1000.0) == (50.0, 0.0)


async def test_check_denies_over_limit(policy):
    tight = team(rate_limit=RateLimitConfig(requests_per_minute=1, tokens_per_minute=0))
    with pytest.raises(RateLimitError):
        await policy.check(tight, request())


async def test_check_allows_within_limit(policy):
    await policy.check(team(), request())


async def test_record_increments_budget(policy):
    t = team()
    await policy.record(t, request(), {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000})
    spend = await policy._budget.current_spend_micro(t.name, "monthly")
    assert spend == 12_500_000


async def test_effective_limits_override(policy):
    t = team(rate_limit=RateLimitConfig(requests_per_minute=100, tokens_per_minute=0))
    await policy._overrides.set_limits("acme", 10, 0)
    rpm, tpm = await policy._effective_limits(t)
    assert rpm == 10
    assert tpm == 0
