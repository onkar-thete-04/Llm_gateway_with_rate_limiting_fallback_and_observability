"""Explicit pre/post-call hook points.

These wrap the provider call so later phases (fallback, circuit breaker,
budget recording) can be added at well-defined points without changing the
request pipeline.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

PreCallHook = Callable[[str, dict[str, Any]], Awaitable[None]]
PostCallHook = Callable[[str, dict[str, Any]], Awaitable[None]]
