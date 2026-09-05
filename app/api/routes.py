"""HTTP routes: the gateway's public API."""
from __future__ import annotations

import logging
import time
from typing import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app import observability
from app.api import schemas
from app.api.deps import get_registry, get_team
from app.config.schema import TeamConfig
from app.core.policy import PolicyError
from app.enrich.enricher import EnrichmentBlockError, enrich
from app.limits.budget import BudgetError
from app.limits.rate_limiter import RateLimitError, estimate_input_tokens
from app.providers.base import ProviderError
from app.routing.router import Route, RouteError, route

logger = logging.getLogger("llm-gateway")

router = APIRouter()


def _unified_error(
    message: str,
    type_: str,
    code: int,
    status_code: int,
    retry_after: int | None = None,
) -> JSONResponse:
    headers = {"Retry-After": str(retry_after)} if retry_after is not None else None
    return JSONResponse(
        status_code=status_code,
        content=schemas.UnifiedError(message=message, type=type_, code=code).model_dump(),
        headers=headers,
    )


@router.post("/v1/chat/completions")
async def chat_completions(
    payload: schemas.UnifiedChatRequest,
    request: Request,
    team: TeamConfig = Depends(get_team),
    registry=Depends(get_registry),
):
    started = time.monotonic()
    provider_name = "none"

    with observability.tracer.start_as_current_span("chat_completions") as span:
        span.set_attribute("team", team.name)
        span.set_attribute("model", payload.model)

        try:
            enrich(payload, team)
            policy = getattr(request.app.state, "policy", None)
            if policy is not None:
                await policy.check(team, payload)
            resolved: Route = route(team, payload.model, registry)
            provider_name = resolved.provider_name
            span.set_attribute("provider", provider_name)
        except EnrichmentBlockError as exc:
            observability.record_request(team.name, provider_name, "blocked")
            return _unified_error(str(exc), "content_filter", 400, 400)
        except RouteError as exc:
            observability.record_request(team.name, provider_name, "error")
            return _unified_error(str(exc), "routing_error", exc.status_code, exc.status_code)
        except RateLimitError as exc:
            observability.record_request(team.name, provider_name, "rate_limited")
            return _unified_error(str(exc), "rate_limit", 429, 429, retry_after=exc.retry_after)
        except BudgetError as exc:
            observability.record_request(team.name, provider_name, "budget_exceeded")
            return _unified_error(str(exc), "budget_exceeded", 429, 429)
        except PolicyError as exc:
            observability.record_request(team.name, provider_name, "rejected")
            return _unified_error(str(exc), "policy_error", exc.status_code, exc.status_code)

        try:
            if payload.stream:
                return StreamingResponse(
                    _stream_response(resolved, payload, team, started, request),
                    media_type="text/event-stream",
                )
            response = await resolved.adapter.complete(payload)
        except ProviderError as exc:
            observability.record_request(team.name, provider_name, "error")
            span.record_exception(exc)
            return _unified_error(str(exc), "provider_error", exc.status_code, exc.status_code)

        policy = getattr(request.app.state, "policy", None)
        if policy is not None:
            await policy.record(
                team,
                payload,
                {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                },
            )

        observability.record_request(team.name, provider_name, "success")
        observability.record_tokens(
            team.name,
            provider_name,
            response.usage.prompt_tokens,
            response.usage.completion_tokens,
        )
        observability.DURATION.labels(team=team.name, provider=provider_name).observe(
            time.monotonic() - started
        )
        return JSONResponse(content=response.model_dump())


async def _stream_response(
    resolved: Route,
    payload: schemas.UnifiedChatRequest,
    team: TeamConfig,
    started: float,
    request: Request,
) -> AsyncIterator[str]:
    provider_name = resolved.provider_name
    full_parts: list[str] = []
    try:
        async for chunk in resolved.adapter.stream(payload):
            for choice in chunk.choices:
                if choice.message.content:
                    full_parts.append(choice.message.content)
            yield f"data: {chunk.model_dump_json()}\n\n"
        yield "data: [DONE]\n\n"
        observability.record_request(team.name, provider_name, "success")
    except ProviderError as exc:
        observability.record_request(team.name, provider_name, "error")
        logger.error("stream failed team=%s provider=%s: %s", team.name, provider_name, exc)
        error_json = schemas.UnifiedError(
            message=str(exc), type="provider_error", code=exc.status_code
        ).model_dump_json()
        yield f"data: {error_json}\n\n"
        yield "data: [DONE]\n\n"
    finally:
        observability.DURATION.labels(team=team.name, provider=provider_name).observe(
            time.monotonic() - started
        )
        logger.info(
            "streamed response team=%s provider=%s chars=%d",
            team.name,
            provider_name,
            len("".join(full_parts)),
        )
        policy = getattr(request.app.state, "policy", None)
        if policy is not None:
            await policy.record(
                team,
                payload,
                {
                    "prompt_tokens": estimate_input_tokens(payload),
                    "completion_tokens": len("".join(full_parts)) // 4,
                },
            )


@router.get("/models")
async def list_models(team: TeamConfig = Depends(get_team)):
    return {"object": "list", "data": [{"id": m} for m in team.allowed_models]}


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
