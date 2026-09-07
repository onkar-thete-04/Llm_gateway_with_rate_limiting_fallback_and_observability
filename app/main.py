"""FastAPI application entry point."""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

import httpx
import redis.asyncio as redis
from fastapi import FastAPI

from app.admin.alerts import AlertManager
from app.admin.audit import AuditLog
from app.admin.overrides import Overrides
from app.admin.routes import router as admin_router
from app.api.routes import router
from app.config.loader import ConfigError, ConfigReloader, ConfigStore, load_config
from app.config.schema import ResilienceConfig
from app.limits.budget import BudgetChecker
from app.limits.bucket import TokenBucket
from app.limits.policy import RateLimitPolicy
from app.limits.pricing import PricingTable
from app.limits.rate_limiter import RateLimiter
from app.providers.registry import ProviderRegistry
from app.resilience.circuit import CircuitRegistry
from app.resilience.fallback import FallbackPlanner
from app.resilience.health import HealthMonitor
from app.resilience.manager import ResilienceManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("llm-gateway")

CONFIG_DIR = os.environ.get("LLM_GATEWAY_CONFIG_DIR", "app/config")
PRICING_PATH = os.environ.get("LLM_GATEWAY_PRICING_PATH", "pricing.yaml")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")


def _build_registry(app: FastAPI, store: ConfigStore) -> None:
    app.state.provider_registry = ProviderRegistry(
        store.config, app.state.http_client, store._secrets
    )


def _build_resilience(app: FastAPI, store: ConfigStore) -> None:
    resilience = store.config.resilience or ResilienceConfig()
    cb = resilience.circuit_breaker
    circuits = CircuitRegistry(cb.failure_threshold, cb.window_seconds, cb.cooldown_seconds)
    health = HealthMonitor(
        lambda: app.state.provider_registry,
        app.state.http_client,
        resilience.health_check,
        app.state.redis,
    )
    app.state.circuits = circuits
    app.state.health = health
    app.state.resilience = ResilienceManager(
        FallbackPlanner(resilience),
        circuits,
        health,
        retries=3,
    )


def _on_reload(app: FastAPI, store: ConfigStore) -> None:
    _build_registry(app, store)
    resilience = store.config.resilience or ResilienceConfig()
    app.state.resilience.set_planner(FallbackPlanner(resilience))


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        store = load_config(CONFIG_DIR)
    except ConfigError as exc:
        raise SystemExit(f"failed to load config: {exc}") from exc

    app.state.config_store = store
    app.state.http_client = httpx.AsyncClient(timeout=httpx.Timeout(120.0))
    app.state.redis = redis.from_url(REDIS_URL, decode_responses=False)
    _build_registry(app, store)

    pricing = PricingTable.load(PRICING_PATH)
    bucket = TokenBucket(app.state.redis)
    rate_limiter = RateLimiter(bucket)
    budget_checker = BudgetChecker(app.state.redis, pricing)
    overrides = Overrides(app.state.redis)
    audit = AuditLog(app.state.redis)
    alerts = AlertManager(app.state.redis, app.state.http_client)

    app.state.pricing = pricing
    app.state.overrides = overrides
    app.state.audit = audit
    app.state.alerts = alerts
    app.state.policy = RateLimitPolicy(
        rate_limiter=rate_limiter,
        budget_checker=budget_checker,
        pricing=pricing,
        overrides=overrides,
        alert_manager=alerts,
    )

    _build_resilience(app, store)
    health_task = asyncio.create_task(app.state.health.run())
    app.state.health_task = health_task

    reloader = ConfigReloader(
        store,
        CONFIG_DIR,
        poll_interval=2.0,
        on_reload=lambda: _on_reload(app, store),
    )
    reloader.start()
    app.state.reloader = reloader

    logger.info("gateway started with %d teams, %d providers",
                len(store.config.teams), len(store.config.providers))

    yield

    reloader.stop()
    health_task.cancel()
    try:
        await health_task
    except asyncio.CancelledError:
        pass
    await app.state.redis.aclose()
    await app.state.http_client.aclose()


app = FastAPI(title="LLM Gateway", version="0.3.0", lifespan=lifespan)
app.include_router(router)
app.include_router(admin_router)
