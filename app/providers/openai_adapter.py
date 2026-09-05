"""OpenAI adapter.

OpenAI's chat-completions format is the gateway's standard format, so this
adapter is mostly a passthrough with explicit field mapping.
"""
from __future__ import annotations

from typing import Any

from app.api.schemas import (
    UnifiedChatRequest,
    UnifiedChatResponse,
    UnifiedChoice,
    UnifiedChoiceMessage,
    UnifiedStreamChunk,
)
from app.config.schema import ProviderConfig

from .base import ProviderAdapter, _choice, _extract_usage


class OpenAIAdapter(ProviderAdapter):
    provider_type = "openai"

    def translate_request(self, req: UnifiedChatRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": req.model,
            "messages": [m.model_dump(exclude_none=True) for m in req.messages],
            "stream": req.stream,
        }
        if req.temperature is not None:
            body["temperature"] = req.temperature
        if req.max_tokens is not None:
            body["max_tokens"] = req.max_tokens
        if req.top_p is not None:
            body["top_p"] = req.top_p
        if req.stop:
            body["stop"] = req.stop
        if req.n != 1:
            body["n"] = req.n
        body.update(req.extra)
        return body

    def translate_response(
        self, raw: dict[str, Any], requested_model: str, request_id: str
    ) -> UnifiedChatResponse:
        choices = raw.get("choices") or []
        out_choices = []
        for i, choice in enumerate(choices):
            message = choice.get("message") or {}
            out_choices.append(
                _choice(
                    index=choice.get("index", i),
                    content=message.get("content"),
                    finish_reason=choice.get("finish_reason"),
                )
            )
        if not out_choices:
            out_choices.append(_choice(0, None, "stop"))
        return UnifiedChatResponse(
            id=request_id,
            model=requested_model,
            choices=out_choices,
            usage=_extract_usage(raw),
        )

    def translate_stream_chunk(
        self, raw: dict[str, Any], requested_model: str, request_id: str
    ) -> UnifiedStreamChunk | None:
        choices = raw.get("choices") or []
        if not choices:
            return None
        delta = choices[0].get("delta") or {}
        content = delta.get("content")
        finish_reason = choices[0].get("finish_reason")
        if content is None and finish_reason is None:
            return None
        return UnifiedStreamChunk(
            id=request_id,
            model=requested_model,
            choices=[
                UnifiedChoice(
                    index=choices[0].get("index", 0),
                    message=UnifiedChoiceMessage(content=content),
                    finish_reason=finish_reason,
                )
            ],
        )
