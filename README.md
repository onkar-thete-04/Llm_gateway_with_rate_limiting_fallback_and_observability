# LLM Gateway — Production API Gateway

Unified proxy in front of all organization LLM calls. Per-team rate limits and
budgets, automatic fallback across providers, and unified observability.

## Overview

The gateway exposes a single OpenAI-compatible `POST /v1/chat/completions`
endpoint. Callers never know which provider served a request — the gateway
normalizes requests and responses across providers.

## Features

- **Unified proxy** — normalize requests/responses across OpenAI, Anthropic,
  Ollama, Groq.
- **Auth + routing** — per-team API keys, allowed models and providers.
- **Streaming passthrough** — transparent SSE forwarding with full-response
  logging for observability.
- **Request enrichment** — per-team system prompts, compliance disclaimers,
  content filters.
- **Rate limiting** — per-team RPM + TPM token buckets (Redis Lua, atomic),
  tiered priority (realtime 70% / batch 30%).
- **Budget caps** — per-team monthly/daily USD spend, warn at 80%, block at
  100%.
- **Admin API** — live status, limit/budget overrides, spending, alerts, audit
  log.
- **Resilience** — health monitoring (30s probes, rolling error-rate + p99),
  fallback chains per model tier, retry with exponential backoff, circuit
  breakers.

## Tech Stack

Python 3.11+, FastAPI, Redis, YAML config, OpenTelemetry, Prometheus,
Docker + docker-compose. See `tech-stack.md`.

## Project Status

| Phase | Scope | Status |
|-------|-------|--------|
| 1 | Unified proxy layer (provider abstraction, auth+routing, streaming, enrichment) | Done |
| 2 | Rate limiting + budgets (Redis token bucket) | Done |
| 3 | Provider fallback + circuit breaker | Done |
| 4 | Observability stack (OTel, Prometheus, Grafana) | Planned |

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

## Run

```powershell
uvicorn app.main:app --reload
```

Copy `config.example/*.yaml` to `app/config/` (or set `LLM_GATEWAY_CONFIG_DIR`).

## Test

```powershell
pytest
```

## Config

YAML config under `app/config/` (see `config.example/`). Hot-reloadable via a
polling watcher (2s interval). Provider API keys are resolved from environment
variables referenced by `api_key_ref`.

- `teams.yaml` — teams, API keys, allowed models/providers, `rate_limit`
  (RPM/TPM, optional per-tier), `budget` (amount/window/warn_at), enrichment.
- `providers.yaml` — provider endpoints, models, `api_key_ref`, plus a
  `resilience` block: `model_tiers`, fallback `tiers` (ordered provider hops),
  `circuit_breaker` (N failures / M seconds / cooldown), `health_check`.
- `pricing.yaml` — per-model USD price per 1M tokens (placeholder values).

Rate limits are enforced fail-closed: if Redis is unreachable the gateway
returns `503` rather than bypassing limits.

### Resilience behavior

- A request maps to a fallback tier via `model_tiers`, yielding an ordered
  provider chain (`tiers`). Primary provider tried first.
- Per provider: up to 3 retries with exponential backoff on retryable errors
  (timeouts, 429, 5xx). Non-retryable errors (auth, content policy) fail
  immediately.
- Retryable errors that exhaust retries fail over to the next provider in the
  chain. Fallback applies before a stream starts; no fallback after the first
  chunk is emitted.
- Circuit breaker opens after N failures in M seconds, stops all traffic,
  then half-opens after cooldown with a single probe.
- Health history persisted to Redis (`health:{provider}:{model}`) as a bounded
  list for post-incident analysis.

## API

- `POST /v1/chat/completions` — OpenAI-compatible chat completions
  (`priority: "realtime" | "batch"` field, default `batch`)
- `GET /models` — list models available to the team
- `GET /health` — health check
- `GET /metrics` — Prometheus metrics

## Admin API

Requires `LLM_GATEWAY_ADMIN_KEY` (env var), sent as `Authorization: Bearer`.

- `GET  /admin/teams/{team}/status` — rate limit + budget status
- `PUT  /admin/teams/{team}/limits` — override RPM/TPM
- `PUT  /admin/teams/{team}/budget` — override budget amount/window
- `GET  /admin/teams/{team}/spending` — spend history
- `GET  /admin/audit` — audit log of admin changes
- `POST /admin/teams/{team}/alerts` — set warn threshold + webhook

## Design Docs

- `docs/superpowers/specs/` — phase design specs
