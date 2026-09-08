import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app import tracing
from app.main import app

TEAM_HEADERS = {"Authorization": "Bearer team_acme_secret_key"}

_EXPORTER = InMemorySpanExporter()
_PROVIDER = TracerProvider()
_PROVIDER.add_span_processor(SimpleSpanProcessor(_EXPORTER))
trace.set_tracer_provider(_PROVIDER)

ATTRS = {
    "team_id",
    "model_requested",
    "model_served",
    "prompt_tokens",
    "completion_tokens",
    "cost_usd",
    "latency_ms",
}

LIFECYCLE_SPANS = {
    "request_receipt",
    "authentication",
    "rate_limit_check",
    "provider_selection",
    "llm_call",
    "response_processing",
    "response_delivery",
}


@pytest.fixture
def trace_provider():
    _EXPORTER.clear()
    yield _EXPORTER
    _EXPORTER.clear()


def test_trace_context_attrs_are_uniform():
    ctx = tracing.TraceContext()
    assert set(ctx.attrs().keys()) == ATTRS


def test_span_carries_uniform_attributes(trace_provider):
    ctx = tracing.TraceContext(team_id="acme", model_requested="gpt-4o")
    with tracing.span("unit_span", ctx):
        pass
    spans = trace_provider.get_finished_spans()
    assert [s.name for s in spans] == ["unit_span"]
    attrs = {k: v for k, v in spans[0].attributes.items()}
    assert set(attrs.keys()) == ATTRS
    assert attrs["team_id"] == "acme"
    assert attrs["model_requested"] == "gpt-4o"
    assert attrs["latency_ms"] >= 0


def test_request_lifecycle_emits_seven_spans(trace_provider):
    with respx.mock:
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "chatcmpl-openai-1",
                    "object": "chat.completion",
                    "model": "gpt-4o",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "hi"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
                },
            )
        )
        with TestClient(app) as client:
            resp = client.post(
                "/v1/chat/completions",
                json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
                headers=TEAM_HEADERS,
            )
        assert resp.status_code == 200

    spans = trace_provider.get_finished_spans()
    names = {s.name for s in spans}
    assert LIFECYCLE_SPANS <= names

    llm = next(s for s in spans if s.name == "llm_call")
    attrs = llm.attributes
    assert attrs["team_id"] == "acme"
    assert attrs["model_requested"] == "gpt-4o"
    assert attrs["model_served"] == "gpt-4o"
    assert attrs["prompt_tokens"] == 7
    assert attrs["completion_tokens"] == 3
    assert attrs["cost_usd"] > 0


def test_inbound_traceparent_parents_root_span(trace_provider):
    trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
    span_id = "00f067aa0ba902b7"
    headers = {
        **TEAM_HEADERS,
        "traceparent": f"00-{trace_id}-{span_id}-01",
    }
    with respx.mock:
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "chatcmpl-openai-1",
                    "object": "chat.completion",
                    "model": "gpt-4o",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "hi"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                },
            )
        )
        with TestClient(app) as client:
            client.post(
                "/v1/chat/completions",
                json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
                headers=headers,
            )

    spans = trace_provider.get_finished_spans()
    root = next(s for s in spans if s.name == "request_receipt")
    assert root.parent is not None
    assert format(root.parent.trace_id, "032x") == trace_id


def test_outbound_provider_request_injects_traceparent(trace_provider):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-openai-1",
                "object": "chat.completion",
                "model": "gpt-4o",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "hi"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    with respx.mock:
        respx.post("https://api.openai.com/v1/chat/completions").mock(side_effect=handler)
        with TestClient(app) as client:
            client.post(
                "/v1/chat/completions",
                json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
                headers=TEAM_HEADERS,
            )

    assert "traceparent" in captured["headers"]
