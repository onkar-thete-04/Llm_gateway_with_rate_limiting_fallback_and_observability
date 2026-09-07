import pytest

from app.config.schema import FallbackHop, ResilienceConfig, TeamConfig
from app.providers.registry import ProviderRegistry
from app.resilience.fallback import FallbackPlanner


@pytest.fixture
def registry(client, secrets, sample_config):
    return ProviderRegistry(sample_config, client, secrets)


@pytest.fixture
def resilience():
    return ResilienceConfig(
        model_tiers={"gpt-4o": "premium", "llama3.1": "standard"},
        tiers={
            "premium": [
                FallbackHop(provider="openai", model="gpt-4o"),
                FallbackHop(provider="anthropic", model="claude-3-5-sonnet"),
            ],
            "standard": [
                FallbackHop(provider="groq", model="groq-llama3"),
                FallbackHop(provider="ollama", model="llama3.1"),
            ],
        },
    )


def test_plan_premium_chain(resilience, acme_team, registry):
    planner = FallbackPlanner(resilience)
    routes = planner.plan(acme_team, "gpt-4o", registry)
    assert [r.provider_name for r in routes] == ["openai", "anthropic"]
    assert routes[1].model == "claude-3-5-sonnet"


def test_plan_without_tier_returns_primary_only(resilience, registry):
    planner = FallbackPlanner(resilience)
    team = TeamConfig(name="open", api_key="k", default_provider="openai")
    routes = planner.plan(team, "gpt-4o-mini", registry)
    assert [r.provider_name for r in routes] == ["openai"]


def test_plan_filters_hop_by_allowed_providers(resilience, registry):
    team = TeamConfig(
        name="t",
        api_key="k",
        allowed_providers=["openai"],
        default_provider="openai",
    )
    planner = FallbackPlanner(resilience)
    routes = planner.plan(team, "gpt-4o", registry)
    assert [r.provider_name for r in routes] == ["openai"]


def test_plan_filters_hop_by_allowed_models(resilience, registry):
    team = TeamConfig(
        name="t",
        api_key="k",
        allowed_models=["gpt-4o"],
        default_provider="openai",
    )
    planner = FallbackPlanner(resilience)
    routes = planner.plan(team, "gpt-4o", registry)
    assert [r.provider_name for r in routes] == ["openai"]
