import fakeredis.aioredis
import pytest

from app.limits.budget import BudgetChecker, BudgetError, window_key
from app.limits.pricing import PricingTable


@pytest.fixture
async def checker():
    redis = fakeredis.aioredis.FakeRedis()
    return BudgetChecker(redis, PricingTable.load("pricing.yaml"))


async def test_check_under_limit_not_warn(checker):
    warn = await checker.check("acme", "gpt-4o", limit_micro=100_000_000, window="monthly", warn_at=0.8)
    assert warn is False


async def test_record_and_warn_zone(checker):
    # Record enough spend to exceed 80% of $100 limit.
    # gpt-4o: input 2.50/1M, output 10.00/1M -> 1M input + 1M output = $12.50
    for _ in range(7):
        await checker.record("acme", "gpt-4o", 1_000_000, 1_000_000, "monthly")
    warn = await checker.check("acme", "gpt-4o", limit_micro=100_000_000, window="monthly", warn_at=0.8)
    assert warn is True


async def test_block_when_limit_hit(checker):
    # 8 records * $12.50 = $100 -> at limit
    for _ in range(8):
        await checker.record("acme", "gpt-4o", 1_000_000, 1_000_000, "monthly")
    with pytest.raises(BudgetError):
        await checker.check("acme", "gpt-4o", limit_micro=100_000_000, window="monthly", warn_at=0.8)


def test_window_key_monthly_and_daily():
    assert len(window_key("monthly")) == 7
    assert len(window_key("daily")) == 10
