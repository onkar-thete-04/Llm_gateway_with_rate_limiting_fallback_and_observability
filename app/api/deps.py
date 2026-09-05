"""FastAPI dependencies: authentication and shared state access."""
from __future__ import annotations

from fastapi import HTTPException, Request

from app.config.schema import TeamConfig


def get_config_store(request: Request):
    return request.app.state.config_store


def get_registry(request: Request):
    return request.app.state.provider_registry


def get_team(request: Request) -> TeamConfig:
    store = request.app.state.config_store
    api_key = _extract_api_key(request)
    if not api_key:
        raise HTTPException(status_code=401, detail="missing API key")
    team = store.team_by_key(api_key)
    if team is None:
        raise HTTPException(status_code=401, detail="invalid API key")
    return team


def _extract_api_key(request: Request) -> str | None:
    auth = request.headers.get("Authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[len("bearer "):].strip()
    return request.headers.get("x-api-key")
