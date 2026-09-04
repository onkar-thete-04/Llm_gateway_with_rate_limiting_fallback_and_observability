# LLM Gateway

## What This Is

A production API gateway that sits in front of all of the organization's LLM calls. It presents an OpenAI-compatible API, authenticates teams by API key, enforces per-team rate limits and dollar budgets, automatically falls back to an alternate Groq model when the primary provider rate-limits requests, and provides unified observability across every LLM interaction.

## Core Value

Teams get governed, observable access to LLMs through a single gateway that enforces their limits and budgets and keeps requests flowing when a provider rate-limits.

## Requirements

### Validated

(None yet — ship to validate)

### Active

- [ ] OpenAI-compatible `/v1/chat/completions` endpoint
- [ ] Per-team API key authentication (invalid/missing → 401)
- [ ] YAML configuration with hot reload (teams, keys, models, limits, budgets, prices, fallback chains)
- [ ] Streaming (SSE) and non-streaming passthrough to Groq
- [ ] Per-team Redis token-bucket rate limiting (RPM + TPM), atomic, 429 + Retry-After
- [ ] Per-team monthly/daily dollar budgets (cost = tokens × YAML price table); 80% warn, 100% block
- [ ] Fallback to alternate Groq model on provider 429
- [ ] OpenTelemetry traces + Prometheus metrics + Grafana dashboard
- [ ] Docker + docker-compose deployment (gateway, Redis, Prometheus, Grafana)

### Out of Scope

- Multiple providers (OpenAI, Anthropic) — Groq only for now
- Admin API / UI for team management — YAML only
- Fallback on provider outage (5xx/timeout) — fallback on 429 only
- OAuth / SSO — per-team API keys only
- Queueing / request retry — hard reject with standard errors

## Context

- Python 3.11+, FastAPI as the proxy
- Redis for rate limiting + budget tracking
- YAML config with hot reload
- OpenTelemetry + Prometheus + Grafana for observability
- Groq as the sole LLM provider (fallback = alternate Groq model)
- Docker + docker-compose for containerization
- Small scale at launch (<10 teams)

## Constraints

- **Tech stack**: Python 3.11+, FastAPI, Redis, OpenTelemetry, Prometheus, Grafana, Groq, Docker
- **Provider**: Groq only — no OpenAI/Anthropic routing yet
- **Config**: YAML only (no admin UI/API)
- **Scale**: Small (<10 teams) at launch

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| OpenAI-compatible API | Drop-in adoption — teams keep existing SDKs | — Pending |
| Per-team API keys | Map key → team for limits/budgets | — Pending |
| Redis token buckets (RPM + TPM) | Atomic, distributed-safe rate limiting | — Pending |
| Monthly/daily dollar budgets | Cost = tokens × YAML price table; 80% warn, 100% block | — Pending |
| Fallback on 429 only | Alternate Groq model; outages (5xx/timeout) return error | — Pending |
| Groq only for now | Single provider simplifies v1 | — Pending |

---
*Last updated: 2026-09-04 after initialization*
