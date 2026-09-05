"""Budget alert delivery.

Deduplicates alerts per team+window and optionally POSTs a webhook. Webhook
failures are logged, never raised.
"""
from __future__ import annotations

import logging

import httpx
from redis.asyncio import Redis

logger = logging.getLogger("llm-gateway")

_SENT_KEY = "alert:sent:{team}:{window}"
_SENT_TTL = 30 * 24 * 3600


class AlertManager:
    def __init__(self, redis: Redis, client: httpx.AsyncClient) -> None:
        self._redis = redis
        self._client = client

    async def notify(
        self,
        team: str,
        window: str,
        spend_micro: int,
        limit_micro: int,
        webhook: str | None,
    ) -> None:
        sent_key = _SENT_KEY.format(team=team, window=window)
        already = await self._redis.get(sent_key)
        if already:
            return
        await self._redis.set(sent_key, "1", ex=_SENT_TTL)
        if not webhook:
            logger.info(
                "budget alert team=%s window=%s spend=%.4f limit=%.4f",
                team,
                window,
                spend_micro / 1_000_000,
                limit_micro / 1_000_000,
            )
            return
        try:
            await self._client.post(
                webhook,
                json={
                    "team": team,
                    "window": window,
                    "spend_usd": spend_micro / 1_000_000,
                    "limit_usd": limit_micro / 1_000_000,
                    "event": "budget_warning",
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("alert webhook failed for team=%s: %s", team, exc)
