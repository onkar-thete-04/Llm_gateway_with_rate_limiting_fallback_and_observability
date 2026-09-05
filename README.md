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
- **Resilience** (later phases) — rate limiting, budgets, provider fallback,
  circuit breaker.

## Tech Stack

Python 3.11+, FastAPI, Redis, YAML config, OpenTelemetry, Prometheus,
Docker + docker-compose. See `tech-stack.md`.

## Project Status

| Phase | Scope | Status |
|-------|-------|--------|
| 1 | Unified proxy layer (provider abstraction, auth+routing, streaming, enrichment) | Done |
| 2 | Rate limiting + budgets (Redis token bucket) | Planned |
| 3 | Provider fallback + circuit breaker | Planned |
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

## API

- `POST /v1/chat/completions` — OpenAI-compatible chat completions
- `GET /models` — list models available to the team
- `GET /health` — health check
- `GET /metrics` — Prometheus metrics

## Design Docs

- `docs/superpowers/specs/` — phase design specs
