import fakeredis.aioredis
import httpx
import pytest

import app.main as main
from app.config.schema import (
    GatewayConfig,
    ProviderConfig,
    TeamConfig,
)


@pytest.fixture(autouse=True)
def _fake_redis(monkeypatch):
    monkeypatch.setattr(
        main.redis, "from_url", lambda *a, **k: fakeredis.aioredis.FakeRedis()
    )


@pytest.fixture
def client():
    return httpx.AsyncClient()


@pytest.fixture
def secrets():
    return {"OPENAI_API_KEY": "sk-test", "ANTHROPIC_API_KEY": "ant-test", "GROQ_API_KEY": "g-test"}


@pytest.fixture
def acme_team():
    return TeamConfig(
        name="acme",
        api_key="team_acme_secret_key",
        allowed_models=["gpt-4o", "claude-3-5-sonnet", "llama3.1"],
        allowed_providers=["openai", "anthropic", "ollama"],
        default_provider="openai",
    )


@pytest.fixture
def openai_provider():
    return ProviderConfig(
        name="openai",
        type="openai",
        base_url="https://api.openai.com/v1",
        api_key_ref="OPENAI_API_KEY",
        models=["gpt-4o", "gpt-4o-mini"],
    )


@pytest.fixture
def anthropic_provider():
    return ProviderConfig(
        name="anthropic",
        type="anthropic",
        base_url="https://api.anthropic.com/v1",
        api_key_ref="ANTHROPIC_API_KEY",
        models=["claude-3-5-sonnet"],
    )


@pytest.fixture
def ollama_provider():
    return ProviderConfig(
        name="ollama",
        type="ollama",
        base_url="http://localhost:11434",
        models=["llama3.1"],
    )


@pytest.fixture
def sample_config(openai_provider, anthropic_provider, ollama_provider):
    return GatewayConfig(
        teams=[
            TeamConfig(
                name="acme",
                api_key="team_acme_secret_key",
                allowed_models=["gpt-4o", "claude-3-5-sonnet", "llama3.1"],
                allowed_providers=["openai", "anthropic", "ollama"],
                default_provider="openai",
            )
        ],
        providers=[openai_provider, anthropic_provider, ollama_provider],
    )
