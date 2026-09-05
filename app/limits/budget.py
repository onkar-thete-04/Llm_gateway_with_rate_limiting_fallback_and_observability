"""Budget caps: per-team USD spend tracking with warn/block thresholds.

Spend is stored in integer micro-dollars (1 USD = 1_000_000 micro) to avoid
floating-point drift. Cost is computed from the pricing table using real
provider token counts.
"""
from __future__ import annotations

import time

from redis.asyncio import Redis

from app.limits.pricing import PricingTable

_BUDGET_LUA = """
local key = KEYS[1]
local cost_micro = tonumber(ARGV[1])
local ttl = tonumber(ARGV[2])
local current = redis.call('GET', key)
current = tonumber(current) or 0
local new = current + cost_micro
redis.call('SET', key, new)
redis.call('EXPIRE', key, ttl)
return new
"""

_MICRO_PER_USD = 1_000_000


class BudgetError(Exception):
    pass


def window_key(window: str) -> str:
    now = time.gmtime()
    if window == "daily":
        return time.strftime("%Y-%m-%d", now)
    return time.strftime("%Y-%m", now)


def _window_ttl(window: str) -> int:
    if window == "daily":
        return 2 * 24 * 3600
    return 32 * 24 * 3600


class BudgetChecker:
    def __init__(self, redis: Redis, pricing: PricingTable) -> None:
        self._redis = redis
        self._pricing = pricing
        self._script = redis.register_script(_BUDGET_LUA)

    async def current_spend_micro(self, team_name: str, window: str) -> int:
        key = f"budget:{team_name}:{window_key(window)}"
        value = await self._redis.get(key)
        return int(value or 0)

    async def check(
        self,
        team_name: str,
        model: str,
        limit_micro: int,
        window: str,
        warn_at: float,
    ) -> bool:
        """Return True if the team is in the warning zone. Raise BudgetError
        when the cap is exceeded."""
        spend = await self.current_spend_micro(team_name, window)
        if limit_micro is not None and spend >= limit_micro:
            raise BudgetError(
                f"budget cap exceeded for team '{team_name}' "
                f"({spend / _MICRO_PER_USD:.4f} USD spent)"
            )
        if limit_micro is not None and warn_at > 0 and spend >= limit_micro * warn_at:
            return True
        return False

    async def record(
        self,
        team_name: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        window: str,
    ) -> int:
        """Record cost and return new spend in micro-dollars."""
        cost_usd = self._pricing.cost_usd(model, prompt_tokens, completion_tokens)
        cost_micro = int(round(cost_usd * _MICRO_PER_USD))
        key = f"budget:{team_name}:{window_key(window)}"
        result = await self._script(
            keys=[key],
            args=[cost_micro, _window_ttl(window)],
        )
        return int(result)
