"""Provider registry.

Maps a provider name to a constructed adapter. Rebuilt when configuration is
reloaded.
"""
from __future__ import annotations

from typing import Mapping

import httpx

from app.config.schema import GatewayConfig, ProviderConfig

from .anthropic_adapter import AnthropicAdapter
from .base import ProviderAdapter, ProviderError
from .groq_adapter import GroqAdapter
from .ollama_adapter import OllamaAdapter
from .openai_adapter import OpenAIAdapter


_ADAPTERS = {
    "openai": OpenAIAdapter,
    "groq": GroqAdapter,
    "anthropic": AnthropicAdapter,
    "ollama": OllamaAdapter,
}


class ProviderRegistry:
    def __init__(
        self,
        config: GatewayConfig,
        client: httpx.AsyncClient,
        secrets: Mapping[str, str],
    ) -> None:
        self._adapters: dict[str, ProviderAdapter] = {}
        for provider_config in config.providers:
            adapter_cls = _ADAPTERS.get(provider_config.type)
            if adapter_cls is None:
                raise ProviderError(f"unknown provider type: {provider_config.type}")
            self._adapters[provider_config.name] = adapter_cls(provider_config, client, secrets)

    def get(self, name: str) -> ProviderAdapter:
        adapter = self._adapters.get(name)
        if adapter is None:
            raise ProviderError(f"provider not registered: {name}")
        return adapter

    def names(self) -> list[str]:
        return list(self._adapters.keys())
