import fakeredis.aioredis
import httpx
import pytest

from app.api.schemas import UnifiedChatRequest, UnifiedMessage
from app.config.schema import HealthCheckConfig, TeamConfig
from app.providers.base import ProviderError
from app.resilience.circuit import CircuitRegistry, CircuitState
from app.resilience.fallback import FallbackPlanner
from app.resilience.health import HealthMonitor
from app.resilience.manager import ResilienceManager
from app.routing.router import Route


class _FakeAdapter:
    def __init__(self, name, retryable_failures=0, non_retryable=False):
        self.name = name
        self.calls = 0
        self.retryable_failures = retryable_failures
        self.non_retryable = non_retryable

    async def complete(self, req):
        self.calls += 1
        if self.non_retryable:
            raise ProviderError("auth failed", status_code=401, retryable=False)
        if self.calls <= self.retryable_failures:
            raise ProviderError("flaky", status_code=502, retryable=True)
        return {"ok": True, "model": req.model}


@pytest.fixture
async def manager():
    redis = fakeredis.aioredis.FakeRedis()
    client = httpx.AsyncClient()
    health = HealthMonitor(lambda: None, client, HealthCheckConfig(), redis)
    circuits = CircuitRegistry(5, 60.0, 30.0)
    manager = ResilienceManager(FallbackPlanner(None), circuits, health, retries=3)
    yield manager, circuits
    await client.aclose()


@pytest.fixture
def team():
    return TeamConfig(name="acme", api_key="k")


def _routes(*adapters):
    return [Route(adapter=a, provider_name=a.name, model="gpt-4o") for a in adapters]


async def test_primary_failure_falls_back(manager, team, monkeypatch):
    manager, _ = manager
    monkeypatch.setattr("app.resilience.retry.asyncio.sleep", _no_sleep)
    primary = _FakeAdapter("openai", retryable_failures=3)
    fallback = _FakeAdapter("anthropic")
    response, provider, model = await manager.execute(team, _req(), _routes(primary, fallback))
    assert provider == "anthropic"
    assert model == "gpt-4o"
    assert primary.calls == 3
    assert fallback.calls == 1


async def test_non_retryable_raises_no_fallback(manager, team):
    manager, _ = manager
    primary = _FakeAdapter("openai", non_retryable=True)
    fallback = _FakeAdapter("anthropic")
    with pytest.raises(ProviderError):
        await manager.execute(team, _req(), _routes(primary, fallback))
    assert primary.calls == 1
    assert fallback.calls == 0


async def test_circuit_open_skips_provider(manager, team):
    manager, circuits = manager
    cb = circuits.get("openai")
    cb.record_failure()
    cb.record_failure()
    cb.record_failure()
    cb.record_failure()
    cb.record_failure()
    assert cb.state is CircuitState.OPEN
    primary = _FakeAdapter("openai")
    fallback = _FakeAdapter("anthropic")
    response, provider, _ = await manager.execute(team, _req(), _routes(primary, fallback))
    assert provider == "anthropic"
    assert primary.calls == 0


async def test_all_fail_raises_last_error(manager, team, monkeypatch):
    manager, _ = manager
    monkeypatch.setattr("app.resilience.retry.asyncio.sleep", _no_sleep)
    primary = _FakeAdapter("openai", retryable_failures=3)
    with pytest.raises(ProviderError):
        await manager.execute(team, _req(), _routes(primary))


class _FakeStreamAdapter:
    def __init__(self, name, fail=False):
        self.name = name
        self.fail = fail

    async def stream(self, req):
        if self.fail:
            raise ProviderError("down", status_code=502, retryable=True)
        yield "chunk-1"
        yield "chunk-2"


async def test_stream_falls_back_before_first_chunk(manager, monkeypatch):
    manager, _ = manager
    monkeypatch.setattr("app.resilience.retry.asyncio.sleep", _no_sleep)
    monkeypatch.setattr("app.resilience.manager.asyncio.sleep", _no_sleep)
    primary = _FakeStreamAdapter("openai", fail=True)
    fallback = _FakeStreamAdapter("anthropic")
    chunks = [c async for c in manager.execute_stream(_req(), _routes(primary, fallback))]
    assert chunks == ["chunk-1", "chunk-2"]


def _req():
    return UnifiedChatRequest(model="gpt-4o", messages=[UnifiedMessage(role="user", content="hi")])


async def _no_sleep(_):
    return None
