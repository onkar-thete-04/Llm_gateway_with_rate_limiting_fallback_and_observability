"""Configuration models for the LLM gateway.

Teams and providers are configured in YAML and loaded into these pydantic
models. Rate-limit and budget fields are declared here so later phases can
enforce them without a schema migration, but they are not enforced in Phase 1.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ContentFilterRule(BaseModel):
    """A simple substring/regex content filter rule.

    ``pattern`` is matched against user message content. ``block`` determines
    whether a match rejects the request or merely flags it.
    """

    pattern: str
    block: bool = True


class ContentFilterConfig(BaseModel):
    rules: list[ContentFilterRule] = Field(default_factory=list)


class EnrichmentConfig(BaseModel):
    """Per-team request enrichment policy. All fields optional."""

    system_prompt: str | None = None
    disclaimer: str | None = None
    content_filter: ContentFilterConfig | None = None


class TierLimitConfig(BaseModel):
    requests_per_minute: int | None = None
    tokens_per_minute: int | None = None


class RateLimitConfig(BaseModel):
    """Per-team rate limits. Total capacity split 70/30 between realtime and
    batch tiers unless per-tier limits are provided."""

    requests_per_minute: int | None = None
    tokens_per_minute: int | None = None
    tiers: dict[str, TierLimitConfig] = Field(default_factory=dict)


class BudgetConfig(BaseModel):
    """Per-team USD spend ceiling."""

    amount_usd: float | None = None
    window: Literal["monthly", "daily"] = "monthly"
    warn_at: float = 0.8
    alert_webhook: str | None = None


class TeamConfig(BaseModel):
    name: str
    api_key: str
    allowed_models: list[str] = Field(default_factory=list)
    allowed_providers: list[str] = Field(default_factory=list)
    default_provider: str | None = None
    enrichment: EnrichmentConfig | None = None
    rate_limit: RateLimitConfig | None = None
    budget: BudgetConfig | None = None


ProviderType = Literal["openai", "anthropic", "ollama", "groq"]


class ProviderConfig(BaseModel):
    name: str
    type: ProviderType
    base_url: str | None = None
    api_key_ref: str | None = None
    default_headers: dict[str, str] = Field(default_factory=dict)
    models: list[str] = Field(default_factory=list)


class GatewayConfig(BaseModel):
    """Top-level config: teams + providers."""

    teams: list[TeamConfig] = Field(default_factory=list)
    providers: list[ProviderConfig] = Field(default_factory=list)
