# Phase 3 — Fallback & Resilience Layer (Design)

Date: 2026-09-07
Status: Implemented

## Goal

Keep requests flowing when a provider degrades or goes down: health
monitoring, automatic fallback routing via model-tier chains, retry with
exponential backoff, and circuit breakers.

## Scope

1. **3.1 Health checking** — background probe every 30s per provider (raw
   `httpx` GET to base URL — no auth, no tokens). Rolling windows track error
   rate + p99 latency; derive `healthy | degraded | down` per provider-model.
   History persisted to Redis as a bounded list.
2. **3.2 Fallback routing** — fallback chains defined per model tier (not per
   model). Example: `gpt-4o` → `premium` tier → `openai/gpt-4o` then
   `anthropic/claude-3-5-sonnet`.
3. **3.3 Retry + exponential backoff** — up to 3 retries, base 0.5s × 2^n +
   jitter. Retryable = timeouts, 429, 5xx; non-retryable = auth (401/403),
   content policy (400), other 4xx. Only retryable errors that exhaust retries
   trigger fallback.
4. **3.4 Circuit breakers** — open after N failures in M seconds, stop all
   traffic, half-open single probe after cooldown, close on success. Every
   transition logged.

## Decisions (locked)

- Fallback chains explicit per model tier: `model_tiers` maps model → tier,
  `tiers` maps tier → ordered `{provider, model}` hops.
- 3 retries, exponential backoff + jitter.
- Circuit breaker thresholds configurable (`failure_threshold`,
  `window_seconds`, `cooldown_seconds`).
- `ProviderError` gains `retryable` flag derived from upstream status; real
  `httpx.TimeoutException`/`RequestError` mapped to retryable errors.
- Retry/fallback applies before a stream starts; no fallback after the first
  chunk is emitted (bytes already sent).
- Health probe = lightweight raw ping (no auth token).
- Health history = Redis-persisted bounded list (`health:{provider}:{model}`).

## Architecture

```
app/resilience/
  __init__.py
  retry.py       # is_retryable, retry_with_backoff, backoff_delay
  circuit.py     # CircuitBreaker (closed/open/half-open), CircuitRegistry
  health.py      # HealthMonitor (probes, rolling windows, Redis history)
  fallback.py    # FallbackPlanner (model-tier chains -> ordered routes)
  manager.py     # ResilienceManager (attempt -> retry -> fallback)
```

Pipeline: `route` (primary) + `FallbackPlanner.plan` (ordered chain) →
`ResilienceManager.execute` / `execute_stream` gates each hop on circuit +
health, retries, falls back on exhausted retryable errors.

## Config

`resilience` block in `providers.yaml`:

```yaml
resilience:
  model_tiers: {gpt-4o: premium, llama3.1: standard}
  tiers:
    premium:
      - {provider: openai, model: gpt-4o}
      - {provider: anthropic, model: claude-3-5-sonnet}
  circuit_breaker: {failure_threshold: 5, window_seconds: 60, cooldown_seconds: 30}
  health_check: {interval_seconds: 30, degraded_error_rate: 0.05,
                 down_error_rate: 0.25, p99_threshold_ms: 5000, history_size: 100}
```

## Testing

- `test_retry.py` — classification, attempt counts, non-retryable short-circuit.
- `test_circuit.py` — state transitions, sliding-window pruning, half-open probe.
- `test_fallback.py` — tier ordering, team allow-list filtering.
- `test_health.py` — status derivation, Redis history, probe cycle.
- `test_resilience_manager.py` — retry-then-fallback, no-fallback on
  non-retryable, circuit-open skip, stream fallback before first chunk.
