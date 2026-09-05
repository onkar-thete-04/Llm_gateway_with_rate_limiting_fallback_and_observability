"""FastAPI application entry point."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.api.routes import router
from app.config.loader import ConfigError, ConfigReloader, ConfigStore, load_config
from app.core.policy import NoopPolicy
from app.providers.registry import ProviderRegistry

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("llm-gateway")

CONFIG_DIR = os.environ.get("LLM_GATEWAY_CONFIG_DIR", "app/config")


def _build_registry(app: FastAPI, store: ConfigStore) -> None:
    app.state.provider_registry = ProviderRegistry(
        store.config, app.state.http_client, store._secrets
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        store = load_config(CONFIG_DIR)
    except ConfigError as exc:
        raise SystemExit(f"failed to load config: {exc}") from exc

    app.state.config_store = store
    app.state.http_client = httpx.AsyncClient(timeout=httpx.Timeout(120.0))
    app.state.policy = NoopPolicy()
    _build_registry(app, store)

    reloader = ConfigReloader(
        store,
        CONFIG_DIR,
        poll_interval=2.0,
        on_reload=lambda: _build_registry(app, store),
    )
    reloader.start()
    app.state.reloader = reloader

    logger.info("gateway started with %d teams, %d providers",
                len(store.config.teams), len(store.config.providers))

    yield

    reloader.stop()
    await app.state.http_client.aclose()


app = FastAPI(title="LLM Gateway", version="0.1.0", lifespan=lifespan)
app.include_router(router)
