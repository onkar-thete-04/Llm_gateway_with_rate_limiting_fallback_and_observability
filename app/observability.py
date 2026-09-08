"""Observability: OpenTelemetry tracing + Prometheus metrics.

Phase 4.2 exposes the gateway's operational metrics: request rate, error rate,
latency histograms, token throughput, cost, fallback triggers, and circuit
breaker state. Rates and percentiles are derived in PromQL (``rate()`` and
``histogram_quantile()``) rather than computed in-process.
"""
from __future__ import annotations

from opentelemetry import trace
from prometheus_client import Counter, Gauge, Histogram

tracer = trace.get_tracer("llm-gateway")

REQUESTS = Counter(
    "llm_gateway_requests_total",
    "Total gateway requests",
    ["team", "model", "provider", "status"],
)

ERRORS = Counter(
    "llm_gateway_request_errors_total",
    "Gateway request errors by type",
    ["team", "model", "provider", "error_type"],
)

DURATION = Histogram(
    "llm_gateway_request_duration_seconds",
    "Request latency in seconds",
    ["team", "provider"],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0],
)

TOKENS = Counter(
    "llm_gateway_tokens_total",
    "Tokens consumed, by type",
    ["team", "provider", "type"],
)

COST = Counter(
    "llm_gateway_cost_usd_total",
    "Accumulated spend in USD",
    ["team"],
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

CIRCUIT_STATE = Gauge(
    "llm_gateway_circuit_breaker_state",
    "Current circuit breaker state (0=closed, 1=open, 2=half_open)",
    ["provider"],
)

CIRCUIT_TRANSITIONS = Counter(
    "llm_gateway_circuit_breaker_transitions_total",
    "Circuit breaker state transitions",
    ["provider", "from", "to"],
)

PROVIDER_STATUS = Gauge(
    "llm_gateway_provider_status",
    "Provider health status",
    ["provider"],
)

BUDGET_LIMIT = Gauge(
    "llm_gateway_budget_limit_usd",
    "Team budget limit in USD",
    ["team"],
)

_HEALTH_STATE_VALUES = {"healthy": 0.0, "degraded": 1.0, "down": 2.0}


def set_provider_status(provider: str, status: str) -> None:
    PROVIDER_STATUS.labels(provider=provider).set(_HEALTH_STATE_VALUES[status])


def set_budget_limit(team: str, limit_usd: float) -> None:
    BUDGET_LIMIT.labels(team=team).set(limit_usd)


_CIRCUIT_STATE_VALUES = {"closed": 0.0, "open": 1.0, "half_open": 2.0}


def record_request(team: str, model: str, provider: str, status: str) -> None:
    REQUESTS.labels(team=team, model=model, provider=provider, status=status).inc()


def record_error(team: str, model: str, provider: str, error_type: str) -> None:
    ERRORS.labels(
        team=team, model=model, provider=provider, error_type=error_type
    ).inc()


def record_tokens(team: str, provider: str, prompt: int, completion: int) -> None:
    TOKENS.labels(team=team, provider=provider, type="prompt").inc(prompt)
    TOKENS.labels(team=team, provider=provider, type="completion").inc(completion)


def record_cost(team: str, cost_usd: float) -> None:
    COST.labels(team=team).inc(cost_usd)


def set_circuit_state(provider: str, state: str) -> None:
    CIRCUIT_STATE.labels(provider=provider).set(_CIRCUIT_STATE_VALUES[state])


def record_circuit_transition(provider: str, from_state: str, to_state: str) -> None:
    CIRCUIT_TRANSITIONS.labels(provider, from_state, to_state).inc()
