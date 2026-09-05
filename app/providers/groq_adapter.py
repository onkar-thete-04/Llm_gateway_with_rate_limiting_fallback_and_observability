"""Groq adapter.

Groq exposes an OpenAI-compatible chat completions endpoint, so this adapter
inherits the OpenAI adapter unchanged.
"""
from __future__ import annotations

from .openai_adapter import OpenAIAdapter


class GroqAdapter(OpenAIAdapter):
    provider_type = "groq"
