"""Admin overrides stored in Redis.

Dynamic limit/budget adjustments live in Redis so they apply without a restart.
Redis persistence (appendonly) preserves them across Redis restarts.
"""
from __future__ import annotations

from typing import Any

from redis.asyncio import Redis

_LIMITS_KEY = "admin:override:{team}:limits"
_BUDGET_KEY = "admin:override:{team}:budget"


class Overrides:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def get_limits(self, team: str) -> dict[str, Any] | None:
        values = await self._redis.hgetall(_LIMITS_KEY.format(team=team))
        if not values:
            return None
        return {
            "rpm": int(values.get(b"rpm") or 0),
            "tpm": int(values.get(b"tpm") or 0),
        }

    async def set_limits(self, team: str, rpm: int, tpm: int) -> None:
        await self._redis.hset(
            _LIMITS_KEY.format(team=team), mapping={"rpm": rpm, "tpm": tpm}
        )

    async def clear_limits(self, team: str) -> None:
        await self._redis.delete(_LIMITS_KEY.format(team=team))

    async def get_budget(self, team: str) -> dict[str, Any] | None:
        values = await self._redis.hgetall(_BUDGET_KEY.format(team=team))
        if not values:
            return None
        return {
            "amount_usd": float(values.get(b"amount_usd") or 0),
            "window": (values.get(b"window") or b"monthly").decode(),
            "warn_at": float(values.get(b"warn_at") or 0.8),
            "alert_webhook": _decode_optional(values.get(b"alert_webhook")),
        }

    async def set_budget(
        self,
        team: str,
        amount_usd: float,
        window: str,
        warn_at: float = 0.8,
        alert_webhook: str | None = None,
    ) -> None:
        await self._redis.hset(
            _BUDGET_KEY.format(team=team),
            mapping={
                "amount_usd": amount_usd,
                "window": window,
                "warn_at": warn_at,
                "alert_webhook": alert_webhook or "",
            },
        )

    async def clear_budget(self, team: str) -> None:
        await self._redis.delete(_BUDGET_KEY.format(team=team))


def _decode_optional(value: bytes | None) -> str | None:
    if value is None:
        return None
    decoded = value.decode()
    return decoded or None
