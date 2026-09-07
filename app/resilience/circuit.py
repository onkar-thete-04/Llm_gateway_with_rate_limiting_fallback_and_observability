"""Circuit breaker state machine per provider.

closed → open (N failures within M seconds) → half_open (after cooldown) →
closed (probe success) or open (probe failure). Every transition is logged.
"""
from __future__ import annotations

import logging
import time
from enum import Enum

from app import observability

logger = logging.getLogger("llm-gateway.circuit")


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Tracks failures in a sliding window and gates requests."""

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        window_seconds: float = 60.0,
        cooldown_seconds: float = 30.0,
    ) -> None:
        self.name = name
        self._threshold = failure_threshold
        self._window = window_seconds
        self._cooldown = cooldown_seconds
        self.state = CircuitState.CLOSED
        self._failures: list[float] = []
        self._opened_at = 0.0
        observability.set_circuit_state(name, CircuitState.CLOSED.value)

    def allow(self) -> bool:
        """Whether a request may proceed right now."""
        now = time.monotonic()
        if self.state is CircuitState.OPEN:
            if now - self._opened_at >= self._cooldown:
                self._transition(CircuitState.HALF_OPEN)
                return True
            return False
        if self.state is CircuitState.HALF_OPEN:
            return False
        return True

    def record_success(self) -> None:
        self._failures.clear()
        if self.state is CircuitState.HALF_OPEN:
            self._transition(CircuitState.CLOSED)

    def record_failure(self) -> None:
        now = time.monotonic()
        if self.state is CircuitState.HALF_OPEN:
            self._opened_at = now
            self._transition(CircuitState.OPEN)
            return
        self._failures.append(now)
        self._prune(now)
        if len(self._failures) >= self._threshold:
            self._opened_at = now
            self._transition(CircuitState.OPEN)

    def _prune(self, now: float) -> None:
        cutoff = now - self._window
        self._failures = [ts for ts in self._failures if ts >= cutoff]

    def _transition(self, new_state: CircuitState) -> None:
        old = self.state
        self.state = new_state
        observability.set_circuit_state(self.name, new_state.value)
        observability.record_circuit_transition(
            self.name, old.value, new_state.value
        )
        logger.info(
            "circuit %s: %s -> %s",
            self.name,
            old.value,
            new_state.value,
        )


class CircuitRegistry:
    """Per-provider circuit breakers, created lazily with shared config."""

    def __init__(self, failure_threshold: int, window_seconds: float, cooldown_seconds: float):
        self._threshold = failure_threshold
        self._window = window_seconds
        self._cooldown = cooldown_seconds
        self._circuits: dict[str, CircuitBreaker] = {}

    def get(self, provider: str) -> CircuitBreaker:
        circuit = self._circuits.get(provider)
        if circuit is None:
            circuit = CircuitBreaker(
                provider,
                failure_threshold=self._threshold,
                window_seconds=self._window,
                cooldown_seconds=self._cooldown,
            )
            self._circuits[provider] = circuit
        return circuit
