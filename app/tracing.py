"""Distributed tracing instrumentation.

Phase 4.1: OpenTelemetry spans across the request lifecycle — request receipt,
authentication, rate limit check, provider selection, LLM API call, response
processing, and response delivery.

Every span carries a uniform attribute set so downstream analysis (Jaeger,
Tempo, Datadog, ...) has a stable schema:

``team_id``, ``model_requested``, ``model_served``, ``prompt_tokens``,
``completion_tokens``, ``cost_usd``, ``latency_ms``.

Values not yet known when a span starts (token counts, cost) are recorded as
their current best estimate and refined on spans that close later.

W3C ``traceparent`` is extracted from inbound requests (so the gateway joins an
existing distributed trace) and injected into outbound provider calls (so the
gateway's spans link to provider-side traces).
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Iterator

from opentelemetry import trace
from opentelemetry.propagate import extract, inject

_current_ctx: ContextVar[TraceContext | None] = ContextVar(
    "llm_gateway_trace_ctx", default=None
)

tracer = trace.get_tracer("llm-gateway")

SPAN_REQUEST_RECEIPT = "request_receipt"
SPAN_AUTHENTICATION = "authentication"
SPAN_RATE_LIMIT = "rate_limit_check"
SPAN_PROVIDER_SELECTION = "provider_selection"
SPAN_LLM_CALL = "llm_call"
SPAN_RESPONSE_PROCESSING = "response_processing"
SPAN_RESPONSE_DELIVERY = "response_delivery"


@dataclass
class TraceContext:
    """Mutable request trace state shared by every span in a request."""

    team_id: str = ""
    model_requested: str = ""
    model_served: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0

    def attrs(self) -> dict[str, object]:
        return {
            "team_id": self.team_id,
            "model_requested": self.model_requested,
            "model_served": self.model_served or self.model_requested,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cost_usd": self.cost_usd,
            "latency_ms": self.latency_ms,
        }

    def set_tokens(self, prompt: int, completion: int) -> None:
        self.prompt_tokens = prompt
        self.completion_tokens = completion


@contextmanager
def span(name: str, ctx: TraceContext) -> Iterator[trace.Span]:
    """Start a child span carrying the uniform attribute set.

    ``latency_ms`` is computed from the span's own wall-clock duration on exit.
    """
    started = time.monotonic()
    with tracer.start_as_current_span(name) as current:
        apply_attrs(current, ctx)
        yield current
        current.set_attribute("latency_ms", (time.monotonic() - started) * 1000.0)


def apply_attrs(current: trace.Span, ctx: TraceContext) -> None:
    for key, value in ctx.attrs().items():
        current.set_attribute(key, value)


def start_receipt(headers: object) -> tuple[object, trace.Span, TraceContext]:
    """Open the root ``request_receipt`` span as a child of any inbound trace.

    Returns ``(context_manager, span, ctx)``. The caller owns closing the
    context manager once the response is known.
    """
    ctx = TraceContext()
    parent = extract(headers)
    cm = tracer.start_as_current_span(SPAN_REQUEST_RECEIPT, context=parent)
    current = cm.__enter__()
    apply_attrs(current, ctx)
    return cm, current, ctx


def set_ctx(ctx: TraceContext) -> Token:
    """Bind ``ctx`` to the current task so downstream code can find it."""
    return _current_ctx.set(ctx)


def current_ctx() -> TraceContext:
    """Return the active request ``TraceContext`` (creating one if absent)."""
    ctx = _current_ctx.get()
    if ctx is None:
        ctx = TraceContext()
        _current_ctx.set(ctx)
    return ctx


def reset_ctx(token: Token) -> None:
    _current_ctx.reset(token)


def inject_headers(headers: dict[str, str]) -> None:
    """Write the current trace context (``traceparent``) into outbound headers."""
    inject(headers)
