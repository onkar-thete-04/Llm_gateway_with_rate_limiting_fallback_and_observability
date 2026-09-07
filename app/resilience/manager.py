"""Resilience orchestration: attempt → retry → fallback.

For a planned candidate list (see ``FallbackPlanner``), each provider is
gated by its circuit breaker and health status, then attempted with retry +
exponential backoff. Only retryable errors that exhaust retries move the
request to the next candidate; non-retryable errors propagate immediately.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import AsyncIterator

from app import observability
from app.api.schemas import UnifiedChatRequest, UnifiedChatResponse, UnifiedStreamChunk
from app.config.schema import TeamConfig
from app.providers.base import ProviderError
from app.providers.registry import ProviderRegistry
from app.routing.router import Route

from .circuit import CircuitRegistry
from .fallback import FallbackPlanner
from .health import HealthMonitor
from .retry import backoff_delay, retry_with_backoff

logger = logging.getLogger("llm-gateway.resilience")


class ResilienceManager:
    def __init__(
        self,
        planner: FallbackPlanner,
        circuits: CircuitRegistry,
        health: HealthMonitor,
        retries: int = 3,
    ) -> None:
        self._planner = planner
        self._circuits = circuits
        self._health = health
        self._retries = retries

    def plan(self, team: TeamConfig, model: str, registry: ProviderRegistry) -> list[Route]:
        return self._planner.plan(team, model, registry)

    def set_planner(self, planner: FallbackPlanner) -> None:
        self._planner = planner

    async def execute(
        self,
        team: TeamConfig,
        payload: UnifiedChatRequest,
        routes: list[Route],
    ) -> tuple[UnifiedChatResponse, str, str]:
        """Attempt routes in order. Returns (response, provider, model_used)."""
        last_exc: ProviderError | None = None
        for route in routes:
            circuit = self._circuits.get(route.provider_name)
            if not circuit.allow():
                continue
            if self._health.is_down(route.provider_name, route.model):
                continue

            req = payload.model_copy(update={"model": route.model or payload.model})
            started = time.monotonic()
            try:
                response = await retry_with_backoff(
                    lambda: route.adapter.complete(req),
                    attempts=self._retries,
                    on_retry=self._on_retry(route.provider_name),
                )
            except ProviderError as exc:
                if not exc.retryable:
                    raise
                circuit.record_failure()
                await self._health.record(route.provider_name, route.model or "", False, 0.0)
                last_exc = exc
                if route is not routes[-1]:
                    observability.FALLBACKS.labels(team=team.name, provider=route.provider_name).inc()
                logger.warning(
                    "provider %s failed for team=%s: %s",
                    route.provider_name,
                    team.name,
                    exc,
                )
                continue

            circuit.record_success()
            latency_ms = (time.monotonic() - started) * 1000.0
            await self._health.record(route.provider_name, route.model or "", True, latency_ms)
            return response, route.provider_name, req.model

        if last_exc is not None:
            raise last_exc
        raise ProviderError("no healthy provider available", status_code=503, retryable=True)

    async def execute_stream(
        self,
        payload: UnifiedChatRequest,
        routes: list[Route],
    ) -> AsyncIterator[UnifiedStreamChunk]:
        """Stream with handshake retry + fallback; no fallback after first chunk."""
        last_exc: ProviderError | None = None
        for route in routes:
            circuit = self._circuits.get(route.provider_name)
            if not circuit.allow():
                continue
            if self._health.is_down(route.provider_name, route.model):
                continue

            req = payload.model_copy(update={"model": route.model or payload.model})
            for attempt in range(self._retries):
                chunk_emitted = False
                try:
                    async for chunk in route.adapter.stream(req):
                        chunk_emitted = True
                        yield chunk
                    circuit.record_success()
                    await self._health.record(route.provider_name, route.model or "", True, 0.0)
                    return
                except ProviderError as exc:
                    if chunk_emitted or not exc.retryable:
                        raise
                    last_exc = exc
                    if attempt == self._retries - 1:
                        circuit.record_failure()
                        await self._health.record(route.provider_name, route.model or "", False, 0.0)
                        break
                    await self._sleep_backoff(attempt)

        if last_exc is not None:
            raise last_exc
        raise ProviderError("no healthy provider available", status_code=503, retryable=True)

    def _on_retry(self, provider: str):
        async def hook(attempt: int, exc: Exception) -> None:
            observability.RETRIES.labels(provider=provider).inc()
            logger.info("retry %d provider=%s: %s", attempt, provider, exc)

        return hook

    async def _sleep_backoff(self, attempt: int) -> None:
        await asyncio.sleep(backoff_delay(attempt))
