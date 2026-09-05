"""Unified request/response schemas.

The gateway speaks OpenAI's chat-completions format to callers. These models
are the single standard format: adapters translate provider payloads into and
out of them.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field


class UnifiedMessage(BaseModel):
    role: str
    content: str | None = None


class UnifiedChatRequest(BaseModel):
    model: str
    messages: list[UnifiedMessage] = Field(min_length=1)
    stream: bool = False
    priority: Literal["realtime", "batch"] = "batch"
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
    n: int = 1
    stop: list[str] | None = None
    # Extra provider-agnostic passthrough fields callers may send.
    extra: dict[str, Any] = Field(default_factory=dict)


class UnifiedChoiceMessage(BaseModel):
    role: str = "assistant"
    content: str | None = None


class UnifiedChoice(BaseModel):
    index: int = 0
    message: UnifiedChoiceMessage = Field(default_factory=UnifiedChoiceMessage)
    finish_reason: str | None = None


class UnifiedUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class UnifiedChatResponse(BaseModel):
    id: str = Field(default_factory=lambda: f"chatcmpl-{uuid.uuid4().hex}")
    object: Literal["chat.completion"] = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    choices: list[UnifiedChoice] = Field(default_factory=list)
    usage: UnifiedUsage = Field(default_factory=UnifiedUsage)


class UnifiedStreamChunk(BaseModel):
    id: str = Field(default_factory=lambda: f"chatcmpl-{uuid.uuid4().hex}")
    object: Literal["chat.completion.chunk"] = "chat.completion.chunk"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    choices: list[UnifiedChoice] = Field(default_factory=list)


class UnifiedError(BaseModel):
    object: Literal["error"] = "error"
    message: str
    type: str
    code: int
