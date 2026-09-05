"""Provider adapter base class.

Each provider adapter normalizes requests into a provider-native payload and
normalizes responses back into the unified OpenAI-compatible format. Callers
never see provider-specific payloads.
"""
from __future__ import annotations

import json
import uuid
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Mapping

import httpx

from app.api.schemas import (
    UnifiedChatRequest,
    UnifiedChatResponse,
    UnifiedChoice,
    UnifiedChoiceMessage,
    UnifiedStreamChunk,
    UnifiedUsage,
)
from app.config.schema import ProviderConfig


class ProviderError(Exception):
    """Raised when a provider call fails in a way the caller can see."""

    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


class ProviderAdapter(ABC):
    provider_type: str = "base"

    def __init__(
        self,
        provider_config: ProviderConfig,
        client: httpx.AsyncClient,
        secrets: Mapping[str, str],
    ) -> None:
        self.config = provider_config
        self.client = client
        self.secrets = secrets

    # -- helpers ---------------------------------------------------------

    def _api_key(self) -> str | None:
        return self.secrets.get(self.config.api_key_ref or "")

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        headers.update(self.config.default_headers)
        api_key = self._api_key()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def _endpoint(self) -> str:
        return f"{self.config.base_url.rstrip('/')}/chat/completions"

    def _new_id(self) -> str:
        return f"chatcmpl-{uuid.uuid4().hex}"

    # -- abstract translate methods -------------------------------------

    @abstractmethod
    def translate_request(self, req: UnifiedChatRequest) -> dict[str, Any]:
        """Convert a unified request into a provider-native JSON body."""

    @abstractmethod
    def translate_response(
        self, raw: dict[str, Any], requested_model: str, request_id: str
    ) -> UnifiedChatResponse:
        """Convert a provider-native response into a unified response."""

    @abstractmethod
    def translate_stream_chunk(
        self, raw: dict[str, Any], requested_model: str, request_id: str
    ) -> UnifiedStreamChunk | None:
        """Convert one provider-native stream event into a unified chunk,
        or return None to skip."""

    # -- transport -------------------------------------------------------

    async def complete(self, req: UnifiedChatRequest) -> UnifiedChatResponse:
        request_id = self._new_id()
        body = self.translate_request(req)
        resp = await self.client.post(self._endpoint(), json=body, headers=self._headers())
        data = self._parse_json(resp)
        return self.translate_response(data, req.model, request_id)

    async def stream(self, req: UnifiedChatRequest) -> AsyncIterator[UnifiedStreamChunk]:
        request_id = self._new_id()
        body = self.translate_request(req)
        body = {**body, "stream": True}
        async with self.client.stream(
            "POST", self._endpoint(), json=body, headers=self._headers()
        ) as resp:
            async for data in self._iter_stream_events(resp):
                chunk = self.translate_stream_chunk(data, req.model, request_id)
                if chunk is not None:
                    yield chunk

    async def _iter_stream_events(self, resp: httpx.Response) -> AsyncIterator[dict[str, Any]]:
        """Default: OpenAI-style SSE. Subclasses may override for NDJSON or
        custom event shapes."""
        async for line in resp.aiter_lines():
            line = line.strip()
            if not line or not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if payload == "[DONE]":
                break
            try:
                yield json.loads(payload)
            except json.JSONDecodeError:
                continue

    def _parse_json(self, resp: httpx.Response) -> dict[str, Any]:
        if resp.is_error:
            detail = resp.text
            try:
                detail = str(resp.json())
            except Exception:
                pass
            raise ProviderError(
                f"provider returned {resp.status_code}: {detail}",
                status_code=502,
            )
        try:
            return resp.json()
        except json.JSONDecodeError as exc:
            raise ProviderError("provider returned non-JSON response") from exc


def _choice(
    index: int,
    content: str | None,
    finish_reason: str | None,
) -> UnifiedChoice:
    return UnifiedChoice(
        index=index,
        message=UnifiedChoiceMessage(role="assistant", content=content),
        finish_reason=finish_reason,
    )


def _usage(prompt_tokens: int, completion_tokens: int) -> UnifiedUsage:
    return UnifiedUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )


def _extract_message_content(raw: dict[str, Any]) -> str | None:
    """Pull assistant text out of a provider-native completion response."""
    choices = raw.get("choices") or []
    if not choices:
        return None
    message = choices[0].get("message") or {}
    return message.get("content")


def _extract_usage(raw: dict[str, Any]) -> UnifiedUsage:
    usage = raw.get("usage") or {}
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    return _usage(prompt, completion)
