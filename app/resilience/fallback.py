"""Fallback routing: build ordered candidate lists from model-tier chains.

A model maps to a tier via ``model_tiers``. Each tier has an ordered chain of
``{provider, model}`` hops. The primary provider (from existing routing) is
tried first; remaining hops follow in config order.
"""
from __future__ import annotations

from app.config.schema import ResilienceConfig, TeamConfig
from app.providers.registry import ProviderRegistry
from app.routing.router import Route, route


class FallbackPlanner:
    def __init__(self, resilience: ResilienceConfig | None) -> None:
        self._model_tiers = (resilience or ResilienceConfig()).model_tiers
        self._tiers = (resilience or ResilienceConfig()).tiers

    def plan(
        self,
        team: TeamConfig,
        model: str,
        registry: ProviderRegistry,
    ) -> list[Route]:
        primary = route(team, model, registry)
        tier = self._model_tiers.get(model)
        result = [primary]
        if tier is None:
            return result

        seen = {primary.provider_name}
        for hop in self._tiers.get(tier, []):
            if hop.provider in seen:
                continue
            if team.allowed_providers and hop.provider not in team.allowed_providers:
                continue
            if team.allowed_models and hop.model not in team.allowed_models:
                continue
            try:
                adapter = registry.get(hop.provider)
            except Exception:  # noqa: BLE001 - unregistered hop skipped
                continue
            result.append(Route(adapter=adapter, provider_name=hop.provider, model=hop.model))
            seen.add(hop.provider)
        return result
