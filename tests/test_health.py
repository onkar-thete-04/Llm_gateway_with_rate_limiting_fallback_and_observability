import fakeredis.aioredis
import httpx
import pytest

from app.config.schema import HealthCheckConfig
from app.resilience.health import HealthMonitor, HealthStatus


@pytest.fixture
async def monitor():
    redis = fakeredis.aioredis.FakeRedis()
    client = httpx.AsyncClient()
    mon = HealthMonitor(lambda: None, client, HealthCheckConfig(history_size=10), redis)
    yield mon
    await client.aclose()


async def test_status_derivation(monitor):
    await monitor.record("openai", "gpt-4o", True, 100.0)
    assert monitor.status("openai", "gpt-4o") is HealthStatus.HEALTHY

    for _ in range(10):
        await monitor.record("openai", "gpt-4o", False, 0.0)
    assert monitor.status("openai", "gpt-4o") is HealthStatus.DOWN


async def test_degraded_on_error_rate(monitor):
    for _ in range(8):
        await monitor.record("openai", "gpt-4o", True, 50.0)
    for _ in range(2):
        await monitor.record("openai", "gpt-4o", False, 0.0)  # 20% errors
    assert monitor.status("openai", "gpt-4o") is HealthStatus.DEGRADED


async def test_healthy_below_threshold(monitor):
    for _ in range(98):
        await monitor.record("openai", "gpt-4o", True, 50.0)
    await monitor.record("openai", "gpt-4o", False, 0.0)  # ~1% errors
    assert monitor.status("openai", "gpt-4o") is HealthStatus.HEALTHY


async def test_is_down_helper(monitor):
    assert not monitor.is_down("openai", "gpt-4o")
    for _ in range(10):
        await monitor.record("openai", "gpt-4o", False, 0.0)
    assert monitor.is_down("openai", "gpt-4o")


async def test_history_persisted_to_redis(monitor):
    redis = monitor._redis
    await monitor.record("openai", "gpt-4o", True, 123.4)
    entries = await redis.lrange("health:openai:gpt-4o", 0, -1)
    assert len(entries) == 1
    assert b"123.4" in entries[0]


class _FakeAdapter:
    def __init__(self, base_url, models):
        self.config = type("Cfg", (), {"base_url": base_url, "models": models})()


class _FakeRegistry:
    def __init__(self):
        self._adapters = {
            "openai": _FakeAdapter("https://api.openai.com/v1", ["gpt-4o"]),
        }

    def names(self):
        return list(self._adapters)

    def get(self, name):
        return self._adapters[name]


async def test_probe_all_records_and_persists():
    redis = fakeredis.aioredis.FakeRedis()

    def handler(request):
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    mon = HealthMonitor(lambda: _FakeRegistry(), client, HealthCheckConfig(), redis)
    await mon.probe_all()
    assert mon.status("openai", "gpt-4o") is HealthStatus.HEALTHY
    entries = await redis.lrange("health:openai:gpt-4o", 0, -1)
    assert len(entries) == 1
    await client.aclose()
