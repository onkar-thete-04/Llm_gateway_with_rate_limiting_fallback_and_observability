"""Ollama adapter.

Ollama's local API uses NDJSON streaming (one JSON object per line) and a
`/api/chat` endpoint. No authentication required.
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

from app.api.schemas import (
    UnifiedChatRequest,
    UnifiedChatResponse,
    UnifiedChoice,
    UnifiedChoiceMessage,
    UnifiedStreamChunk,
)
from app.config.schema import ProviderConfig

from .base import ProviderAdapter, _choice, _usage


class OllamaAdapter(ProviderAdapter):
    provider_type = "ollama"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        headers.update(self.config.default_headers)
        return headers

    def _endpoint(self) -> str:
        return f"{self.config.base_url.rstrip('/')}/api/chat"

    def translate_request(self, req: UnifiedChatRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": req.model,
            "messages": [m.model_dump(exclude_none=True) for m in req.messages],
            "stream": req.stream,
        }
        options: dict[str, Any] = {}
        if req.temperature is not None:
            options["temperature"] = req.temperature
        if req.max_tokens is not None:
            options["num_predict"] = req.max_tokens
        if req.top_p is not None:
            options["top_p"] = req.top_p
        if options:
            body["options"] = options
        body.update(req.extra)
        return body

    def translate_response(
        self, raw: dict[str, Any], requested_model: str, request_id: str
    ) -> UnifiedChatResponse:
        message = raw.get("message") or {}
        return UnifiedChatResponse(
            id=request_id,
            model=requested_model,
            choices=[_choice(0, message.get("content"), "stop" if raw.get("done") else None)],
            usage=_usage(
                int(raw.get("prompt_eval_count") or 0),
                int(raw.get("eval_count") or 0),
            ),
        )

    def translate_stream_chunk(
        self, raw: dict[str, Any], requested_model: str, request_id: str
    ) -> UnifiedStreamChunk | None:
        message = raw.get("message") or {}
        content = message.get("content")
        if raw.get("done"):
            return UnifiedStreamChunk(
                id=request_id,
                model=requested_model,
                choices=[
                    UnifiedChoice(
                        index=0,
                        message=UnifiedChoiceMessage(content=None),
                        finish_reason="stop",
                    )
                ],
            )
        if not content:
            return None
        return UnifiedStreamChunk(
            id=request_id,
            model=requested_model,
            choices=[
                UnifiedChoice(
                    index=0,
                    message=UnifiedChoiceMessage(content=content),
                )
            ],
        )

    async def _iter_stream_events(self, resp: httpx.Response) -> AsyncIterator[dict[str, Any]]:
        async for line in resp.aiter_lines():
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue
