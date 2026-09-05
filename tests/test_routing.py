import pytest

from app.config.schema import TeamConfig
from app.providers.registry import ProviderRegistry
from app.routing.router import RouteError, route


@pytest.fixture
def registry(client, secrets, sample_config):
    return ProviderRegistry(sample_config, client, secrets)


def test_route_uses_default_provider(acme_team, registry):
    resolved = route(acme_team, "gpt-4o", registry)
    assert resolved.provider_name == "openai"


def test_route_falls_back_to_other_provider(acme_team, registry):
    team = acme_team.model_copy(update={"allowed_providers": ["anthropic"]})
    resolved = route(team, "claude-3-5-sonnet", registry)
    assert resolved.provider_name == "anthropic"


def test_route_rejects_disallowed_model(acme_team, registry):
    with pytest.raises(RouteError) as exc:
        route(acme_team, "gpt-5-ultra", registry)
    assert exc.value.status_code == 403


def test_route_no_provider_available(acme_team, registry):
    team = acme_team.model_copy(update={"allowed_providers": ["openai"]})
    with pytest.raises(RouteError) as exc:
        route(team, "claude-3-5-sonnet", registry)
    assert exc.value.status_code == 503


def test_route_empty_allowed_means_no_restriction(client, secrets, sample_config):
    registry = ProviderRegistry(sample_config, client, secrets)
    team = TeamConfig(name="open", api_key="k", allowed_models=[], allowed_providers=[])
    resolved = route(team, "gpt-4o", registry)
    assert resolved.provider_name == "openai"
