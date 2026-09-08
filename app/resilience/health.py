"""Health monitoring: background probes + rolling error-rate/latency windows.

Every ``interval_seconds`` the monitor pings each provider's base URL (a
lightweight raw request — no auth, no tokens). Probe results plus live
request outcomes feed rolling windows per provider-model from which a status
(healthy / degraded / down) is derived. History is persisted to Redis as a
bounded list for post-incident analysis.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from enum import Enum

import httpx

from app.config.schema import HealthCheckConfig
from app import observability

logger = logging.getLogger("llm-gateway.health")

_WINDOW_SECONDS = 60.0


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"


_STATUS_RANK = {
    HealthStatus.HEALTHY: 0,
    HealthStatus.DEGRADED: 1,
    HealthStatus.DOWN: 2,
}


@dataclass
class Sample:
    ok: bool
    latency_ms: float
    ts: float


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1)))))
    return ordered[index]


class HealthMonitor:
    def __init__(
        self,
        registry_getter,
        http_client: httpx.AsyncClient,
        config: HealthCheckConfig,
        redis,
    ) -> None:
        self._registry_getter = registry_getter
        self._client = http_client
        self._cfg = config
        self._redis = redis
        self._samples: dict[tuple[str, str], list[Sample]] = {}
        self._statuses: dict[tuple[str, str], HealthStatus] = {}
        self._stop = asyncio.Event()

    async def run(self) -> None:
        logger.info("health monitor started (interval=%ss)", self._cfg.interval_seconds)
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._cfg.interval_seconds)
            except asyncio.TimeoutError:
                pass
            if self._stop.is_set():
                break
            try:
                await self.probe_all()
            except Exception:  # noqa: BLE001 - a failed cycle must not kill the loop
                logger.exception("health probe cycle failed")
        logger.info("health monitor stopped")

    async def stop(self) -> None:
        self._stop.set()

    async def probe_all(self) -> None:
        try:
            registry = self._registry_getter()
        except Exception:  # noqa: BLE001
            return
        for name in registry.names():
            try:
                adapter = registry.get(name)
            except Exception:  # noqa: BLE001
                continue
            base_url = adapter.config.base_url
            if not base_url:
                continue
            started = time.monotonic()
            try:
                resp = await self._client.get(base_url, timeout=5.0)
                ok = resp.status_code < 500
            except Exception:  # noqa: BLE001
                ok = False
            latency_ms = (time.monotonic() - started) * 1000.0
            models = adapter.config.models or [""]
            for model in models:
                await self.record(name, model, ok, latency_ms)

    async def record(self, provider: str, model: str, ok: bool, latency_ms: float) -> None:
        key = (provider, model)
        samples = self._samples.setdefault(key, [])
        samples.append(Sample(ok=ok, latency_ms=latency_ms, ts=time.monotonic()))
        cutoff = time.monotonic() - _WINDOW_SECONDS
        samples[:] = [s for s in samples if s.ts >= cutoff]
        self._statuses[key] = self._derive(samples)
        self._export_provider_status(provider)
        await self._persist(provider, model, ok, latency_ms)

    def _export_provider_status(self, provider: str) -> None:
        worst = HealthStatus.HEALTHY
        for (prov, _model), status in self._statuses.items():
            if prov != provider:
                continue
            if _STATUS_RANK[status] > _STATUS_RANK[worst]:
                worst = status
        observability.set_provider_status(provider, worst.value)

    def status(self, provider: str, model: str | None) -> HealthStatus:
        key = (provider, model or "")
        return self._statuses.get(key, HealthStatus.HEALTHY)

    def is_down(self, provider: str, model: str | None) -> bool:
        return self.status(provider, model) is HealthStatus.DOWN

    def _derive(self, samples: list[Sample]) -> HealthStatus:
        if not samples:
            return HealthStatus.HEALTHY
        errors = sum(1 for s in samples if not s.ok)
        error_rate = errors / len(samples)
        if error_rate >= self._cfg.down_error_rate:
            return HealthStatus.DOWN
        latencies = [s.latency_ms for s in samples if s.ok]
        p99 = _percentile(latencies, 99.0)
        if error_rate >= self._cfg.degraded_error_rate or p99 > self._cfg.p99_threshold_ms:
            return HealthStatus.DEGRADED
        return HealthStatus.HEALTHY

    async def _persist(self, provider: str, model: str, ok: bool, latency_ms: float) -> None:
        try:
            entry = json.dumps(
                {
                    "ts": time.time(),
                    "ok": ok,
                    "latency_ms": round(latency_ms, 2),
                    "model": model or None,
                }
            )
            key = f"health:{provider}:{model or '-'}"
            await self._redis.lpush(key, entry)
            await self._redis.ltrim(key, 0, self._cfg.history_size - 1)
        except Exception:  # noqa: BLE001 - history is best-effort
            pass
