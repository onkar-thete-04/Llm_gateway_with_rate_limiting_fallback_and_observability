"""Phase 4.2: Prometheus metrics for the gateway pipeline."""
from __future__ import annotations

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from app import observability
from app.api.schemas import UnifiedChatRequest, UnifiedMessage
from app.config.schema import HealthCheckConfig, TeamConfig
from app.main import app
from app.providers.base import ProviderError
from app.resilience.circuit import CircuitBreaker
from app.resilience.fallback import FallbackPlanner
from app.resilience.health import HealthMonitor
from app.resilience.manager import ResilienceManager
from app.resilience.circuit import CircuitRegistry
from app.routing.router import Route

TEAM_HEADERS = {"Authorization": "Bearer team_acme_secret_key"}


def _openai_ok():
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-openai-1",
            "object": "chat.completion",
            "model": "gpt-4o",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Hello from openai"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
        },
    )


def _success_payload():
    return {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}


def test_metrics_endpoint_exposes_families_after_activity():
    with respx.mock:
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=_openai_ok()
        )
        with TestClient(app) as client:
            client.post(
                "/v1/chat/completions", json=_success_payload(), headers=TEAM_HEADERS
            )
            client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-4o",
                    "messages": [{"role": "user", "content": "here is confidential info"}],
                },
                headers=TEAM_HEADERS,
            )
            resp = client.get("/metrics")

    assert resp.status_code == 200
    body = resp.text
    for name in [
        "llm_gateway_requests_total",
        "llm_gateway_request_errors_total",
        "llm_gateway_request_duration_seconds_bucket",
        "llm_gateway_tokens_total",
        "llm_gateway_cost_usd_total",
    ]:
        assert name in body


def test_requests_counter_records_model_label():
    with respx.mock:
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=_openai_ok()
        )
        with TestClient(app) as client:
            resp = client.post(
                "/v1/chat/completions", json=_success_payload(), headers=TEAM_HEADERS
            )
    assert resp.status_code == 200
    value = REGISTRY.get_sample_value(
        "llm_gateway_requests_total",
        {"team": "acme", "model": "gpt-4o", "provider": "openai", "status": "success"},
    )
    assert value is not None and value >= 1.0


def test_errors_counter_records_error_type():
    with TestClient(app) as client:
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "here is confidential info"}],
            },
            headers=TEAM_HEADERS,
        )
    assert resp.status_code == 400
    value = REGISTRY.get_sample_value(
        "llm_gateway_request_errors_total",
        {
            "team": "acme",
            "model": "gpt-4o",
            "provider": "none",
            "error_type": "content_filter",
        },
    )
    assert value is not None and value >= 1.0


def test_cost_counter_increments_after_success():
    before = (
        REGISTRY.get_sample_value("llm_gateway_cost_usd_total", {"team": "acme"}) or 0.0
    )
    with respx.mock:
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=_openai_ok()
        )
        with TestClient(app) as client:
            resp = client.post(
                "/v1/chat/completions", json=_success_payload(), headers=TEAM_HEADERS
            )
    assert resp.status_code == 200
    after = (
        REGISTRY.get_sample_value("llm_gateway_cost_usd_total", {"team": "acme"}) or 0.0
    )
    assert after > before


def test_duration_histogram_has_latency_buckets():
    expected = [0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0]
    assert list(observability.DURATION._upper_bounds) == expected + [float("inf")]


def test_circuit_breaker_state_gauge_and_transitions():
    cb = CircuitBreaker("openai-metrics", 2, 60.0, 0.0)
    assert (
        REGISTRY.get_sample_value(
            "llm_gateway_circuit_breaker_state", {"provider": "openai-metrics"}
        )
        == 0.0
    )

    cb.record_failure()
    cb.record_failure()

    assert (
        REGISTRY.get_sample_value(
            "llm_gateway_circuit_breaker_transitions_total",
            {"provider": "openai-metrics", "from": "closed", "to": "open"},
        )
        == 1.0
    )
    assert (
        REGISTRY.get_sample_value(
            "llm_gateway_circuit_breaker_state", {"provider": "openai-metrics"}
        )
        == 1.0
    )

    cb.allow()
    assert (
        REGISTRY.get_sample_value(
            "llm_gateway_circuit_breaker_state", {"provider": "openai-metrics"}
        )
        == 2.0
    )

    cb.record_success()
    assert (
        REGISTRY.get_sample_value(
            "llm_gateway_circuit_breaker_state", {"provider": "openai-metrics"}
        )
        == 0.0
    )


class _FailingAdapter:
    def __init__(self, name):
        self.name = name

    async def complete(self, req):
        raise ProviderError("flaky", status_code=502, retryable=True)


class _HealthyAdapter:
    def __init__(self, name):
        self.name = name

    async def complete(self, req):
        return {"ok": True}


@pytest.mark.asyncio
async def test_fallback_counter_increments_on_failover():
    import fakeredis.aioredis

    redis = fakeredis.aioredis.FakeRedis()
    http_client = httpx.AsyncClient()
    health = HealthMonitor(lambda: None, http_client, HealthCheckConfig(), redis)
    manager = ResilienceManager(
        FallbackPlanner(None), CircuitRegistry(5, 60.0, 30.0), health, retries=1
    )

    team = TeamConfig(name="acme-metrics", api_key="k")
    routes = [
        Route(adapter=_FailingAdapter("openai-metrics"), provider_name="openai-metrics", model="gpt-4o"),
        Route(adapter=_HealthyAdapter("anthropic-metrics"), provider_name="anthropic-metrics", model="gpt-4o"),
    ]

    before = (
        REGISTRY.get_sample_value(
            "llm_gateway_fallbacks_total",
            {"team": "acme-metrics", "provider": "openai-metrics"},
        )
        or 0.0
    )
    _, provider, _ = await manager.execute(team, _req(), routes)
    assert provider == "anthropic-metrics"
    after = (
        REGISTRY.get_sample_value(
            "llm_gateway_fallbacks_total",
            {"team": "acme-metrics", "provider": "openai-metrics"},
        )
        or 0.0
    )
    assert after > before
    await http_client.aclose()


def _req():
    return UnifiedChatRequest(
        model="gpt-4o", messages=[UnifiedMessage(role="user", content="hi")]
    )
