"""Audit log of admin changes, kept in Redis."""
from __future__ import annotations

import json
import time
from typing import Any

from redis.asyncio import Redis

_AUDIT_KEY = "admin:audit"
_MAX_ENTRIES = 1000


class AuditLog:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def log(
        self,
        actor: str,
        action: str,
        team: str,
        before: Any = None,
        after: Any = None,
    ) -> None:
        entry = json.dumps(
            {
                "ts": int(time.time()),
                "actor": actor,
                "action": action,
                "team": team,
                "before": before,
                "after": after,
            }
        )
        await self._redis.lpush(_AUDIT_KEY, entry)
        await self._redis.ltrim(_AUDIT_KEY, 0, _MAX_ENTRIES - 1)

    async def entries(self, limit: int = 100) -> list[dict[str, Any]]:
        raw = await self._redis.lrange(_AUDIT_KEY, 0, limit - 1)
        return [json.loads(entry) for entry in raw]
