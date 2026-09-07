"""Routing policy: resolve a team + requested model to a provider adapter.

Enforces per-team ``allowed_models`` and ``allowed_providers``. An empty list
means "no restriction". ``default_provider`` is preferred when it can serve the
model; otherwise the first capable provider is used.

Later phases plug fallback and circuit breaking in at this layer without
changing the request pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.config.schema import TeamConfig
from app.providers.base import ProviderAdapter
from app.providers.registry import ProviderRegistry


class RouteError(Exception):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class Route:
    adapter: ProviderAdapter
    provider_name: str
    model: str | None = None


def _provider_can_serve(adapter: ProviderAdapter, model: str) -> bool:
    models = adapter.config.models
    if not models:
        return True
    return model in models


def route(team: TeamConfig, model: str, registry: ProviderRegistry) -> Route:
    if team.allowed_models and model not in team.allowed_models:
        raise RouteError(f"model '{model}' is not allowed for team '{team.name}'", 403)

    candidates = []
    for name in registry.names():
        if team.allowed_providers and name not in team.allowed_providers:
            continue
        adapter = registry.get(name)
        if _provider_can_serve(adapter, model):
            candidates.append((name, adapter))

    if not candidates:
        raise RouteError(f"no provider available for model '{model}'", 503)

    if team.default_provider:
        for name, adapter in candidates:
            if name == team.default_provider:
                return Route(adapter=adapter, provider_name=name, model=model)

    name, adapter = candidates[0]
    return Route(adapter=adapter, provider_name=name, model=model)
