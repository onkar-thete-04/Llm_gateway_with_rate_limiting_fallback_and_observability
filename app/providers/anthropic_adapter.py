"""Anthropic adapter.

Maps the unified OpenAI-style request onto Anthropic's Messages API and maps
responses back. Streaming uses Anthropic's SSE event stream.
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

from .base import ProviderAdapter, _choice, _usage

_DEFAULT_MAX_TOKENS = 1024

_STOP_REASON_MAP = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
}


class AnthropicAdapter(ProviderAdapter):
    provider_type = "anthropic"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        headers.update(self.config.default_headers)
        api_key = self._api_key()
        if api_key:
            headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
        return headers

    def _endpoint(self) -> str:
        return f"{self.config.base_url.rstrip('/')}/messages"

    def translate_request(self, req: UnifiedChatRequest) -> dict[str, Any]:
        system_parts = [m.content for m in req.messages if m.role == "system" and m.content]
        messages = [
            {"role": m.role, "content": m.content}
            for m in req.messages
            if m.role in ("user", "assistant")
        ]
        body: dict[str, Any] = {
            "model": req.model,
            "messages": messages,
            "max_tokens": req.max_tokens or _DEFAULT_MAX_TOKENS,
            "stream": req.stream,
        }
        if system_parts:
            body["system"] = "\n\n".join(system_parts)
        if req.temperature is not None:
            body["temperature"] = req.temperature
        if req.top_p is not None:
            body["top_p"] = req.top_p
        body.update(req.extra)
        return body

    def translate_response(
        self, raw: dict[str, Any], requested_model: str, request_id: str
    ) -> UnifiedChatResponse:
        content_blocks = raw.get("content") or []
        text = ""
        for block in content_blocks:
            if block.get("type") == "text":
                text += block.get("text", "")
        usage = raw.get("usage") or {}
        stop_reason = _STOP_REASON_MAP.get(raw.get("stop_reason"), raw.get("stop_reason"))
        return UnifiedChatResponse(
            id=request_id,
            model=requested_model,
            choices=[_choice(0, text, stop_reason)],
            usage=_usage(
                int(usage.get("input_tokens") or 0),
                int(usage.get("output_tokens") or 0),
            ),
        )

    def translate_stream_chunk(
        self, raw: dict[str, Any], requested_model: str, request_id: str
    ) -> UnifiedStreamChunk | None:
        event_type = raw.get("type")
        if event_type == "content_block_delta":
            delta = raw.get("delta") or {}
            if delta.get("type") != "text_delta":
                return None
            return UnifiedStreamChunk(
                id=request_id,
                model=requested_model,
                choices=[
                    UnifiedChoice(
                        index=0,
                        message=UnifiedChoiceMessage(content=delta.get("text")),
                    )
                ],
            )
        if event_type == "message_delta":
            stop_reason = _STOP_REASON_MAP.get(
                (raw.get("delta") or {}).get("stop_reason"),
                (raw.get("delta") or {}).get("stop_reason"),
            )
            return UnifiedStreamChunk(
                id=request_id,
                model=requested_model,
                choices=[
                    UnifiedChoice(
                        index=0,
                        message=UnifiedChoiceMessage(content=None),
                        finish_reason=stop_reason,
                    )
                ],
            )
        return None
