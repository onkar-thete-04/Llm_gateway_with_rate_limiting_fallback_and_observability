"""Atomic Redis token bucket.

A single Lua script performs refill + consume atomically, so it is safe across
multiple gateway workers. ``refill_rate`` is tokens per second.
"""
from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis

_TOKEN_BUCKET_LUA = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local requested = tonumber(ARGV[4])
local ttl = tonumber(ARGV[5])

local state = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(state[1])
local ts = tonumber(state[2])
if tokens == nil then tokens = capacity end
if ts == nil then ts = now end

local elapsed = math.max(0, now - ts)
local refilled = math.min(capacity, tokens + elapsed * refill_rate)

if refilled < requested then
    local retry_after = 0
    if refill_rate > 0 then
        retry_after = math.ceil((requested - refilled) / refill_rate)
    end
    redis.call('HMSET', key, 'tokens', refilled, 'ts', now)
    redis.call('EXPIRE', key, ttl)
    return {0, refilled, retry_after}
end

local remaining = refilled - requested
redis.call('HMSET', key, 'tokens', remaining, 'ts', now)
redis.call('EXPIRE', key, ttl)
return {1, remaining, 0}
"""


@dataclass(frozen=True)
class BucketResult:
    allowed: bool
    remaining: float
    retry_after: int


class TokenBucket:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis
        self._script = redis.register_script(_TOKEN_BUCKET_LUA)

    async def take(
        self,
        key: str,
        capacity: float,
        refill_rate: float,
        requested: float,
        ttl: int = 3600,
    ) -> BucketResult:
        if capacity <= 0:
            return BucketResult(allowed=True, remaining=0.0, retry_after=0)
        now = _now_seconds()
        result = await self._script(
            keys=[key],
            args=[capacity, refill_rate, now, requested, ttl],
        )
        allowed, remaining, retry_after = result
        return BucketResult(
            allowed=bool(allowed),
            remaining=float(remaining),
            retry_after=int(retry_after),
        )


def _now_seconds() -> float:
    import time

    return time.time()
