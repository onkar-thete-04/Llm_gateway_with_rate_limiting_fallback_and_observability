"""Policy hook interface for later phases.

Rate limiting (token bucket) and budget enforcement plug in here. In Phase 1
these are no-ops so the pipeline shape is already in place.
"""
from __future__ import annotations

from typing import Protocol

from app.api.schemas import UnifiedChatRequest
from app.config.schema import TeamConfig


class PolicyError(Exception):
    """Raised when a policy (rate limit / budget) rejects a request."""

    def __init__(self, message: str, status_code: int = 429) -> None:
        super().__init__(message)
        self.status_code = status_code


class PolicyHook(Protocol):
    def check(self, team: TeamConfig, req: UnifiedChatRequest) -> None:
        """Enforce pre-call policy. Raises ``PolicyError`` on rejection."""

    def record(self, team: TeamConfig, req: UnifiedChatRequest, usage: object) -> None:
        """Record post-call usage (tokens/cost) for budget accounting."""


class NoopPolicy:
    """Phase-1 default: allow everything, record nothing."""

    def check(self, team: TeamConfig, req: UnifiedChatRequest) -> None:
        return None

    def record(self, team: TeamConfig, req: UnifiedChatRequest, usage: object) -> None:
        return None
