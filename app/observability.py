"""Observability: OpenTelemetry tracing + Prometheus metrics.

Phase 1 instruments the request pipeline. Full OTel collector / Prometheus /
Grafana wiring is a later phase; the tracer falls back to no-op until an
OpenTelemetry SDK provider is configured.
"""
from __future__ import annotations

from opentelemetry import trace
from prometheus_client import Counter, Histogram

tracer = trace.get_tracer("llm-gateway")

REQUESTS = Counter(
    "llm_gateway_requests_total",
    "Total gateway requests",
    ["team", "provider", "status"],
)

DURATION = Histogram(
    "llm_gateway_request_duration_seconds",
    "Request latency in seconds",
    ["team", "provider"],
)

TOKENS = Counter(
    "llm_gateway_tokens_total",
    "Tokens consumed, by type",
    ["team", "provider", "type"],
)

FALLBACKS = Counter(
    "llm_gateway_fallbacks_total",
    "Requests failed over to a fallback provider",
    ["team", "provider"],
)

RETRIES = Counter(
    "llm_gateway_retries_total",
    "Provider call retries",
    ["provider"],
)


def record_request(team: str, provider: str, status: str) -> None:
    REQUESTS.labels(team=team, provider=provider, status=status).inc()


def record_tokens(team: str, provider: str, prompt: int, completion: int) -> None:
    TOKENS.labels(team=team, provider=provider, type="prompt").inc(prompt)
    TOKENS.labels(team=team, provider=provider, type="completion").inc(completion)
