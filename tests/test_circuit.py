import app.resilience.circuit as circuit_mod
from app.resilience.circuit import CircuitBreaker, CircuitState


def test_starts_closed():
    cb = CircuitBreaker("x", 3, 60.0, 30.0)
    assert cb.state is CircuitState.CLOSED
    assert cb.allow()


def test_opens_after_threshold_failures():
    cb = CircuitBreaker("x", 2, 60.0, 30.0)
    cb.record_failure()
    assert cb.state is CircuitState.CLOSED
    cb.record_failure()
    assert cb.state is CircuitState.OPEN
    assert not cb.allow()


def test_failures_pruned_outside_window(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(circuit_mod.time, "monotonic", clock.tick)
    cb = CircuitBreaker("x", 2, 60.0, 30.0)
    clock.t = 0.0
    cb.record_failure()
    clock.t = 70.0
    cb.record_failure()
    assert cb.state is CircuitState.CLOSED
    clock.t = 70.1
    cb.record_failure()
    assert cb.state is CircuitState.OPEN


def test_half_open_single_probe_success_closes():
    cb = CircuitBreaker("x", 2, 60.0, 0.0)
    cb.record_failure()
    cb.record_failure()
    assert cb.state is CircuitState.OPEN
    assert cb.allow()  # becomes half-open, one probe granted
    assert cb.state is CircuitState.HALF_OPEN
    assert not cb.allow()  # no second probe
    cb.record_success()
    assert cb.state is CircuitState.CLOSED
    assert cb.allow()


def test_half_open_probe_failure_reopens():
    cb = CircuitBreaker("x", 2, 60.0, 0.0)
    cb.record_failure()
    cb.record_failure()
    cb.allow()
    assert cb.state is CircuitState.HALF_OPEN
    cb.record_failure()
    assert cb.state is CircuitState.OPEN


class _Clock:
    t = 0.0

    def tick(self):
        return self.t
