# Phase 1 — Unified Proxy Layer (Design)

Date: 2026-09-05
Status: Approved

## Goal

Build a production LLM API gateway that exposes an OpenAI-compatible
`POST /v1/chat/completions` endpoint and normalizes requests/responses across
providers (OpenAI, Anthropic, Ollama, Groq). Phase 1 covers the unified proxy
layer only: provider abstraction, auth + routing, streaming passthrough, and
per-team request enrichment.

Rate limiting, budget enforcement, provider fallback, and circuit breaking are
handled in later phases. Phase 1 only provides clean hook points for them.

## Scope (Phase 1)

1. **1.1 Provider abstraction** — unified interface normalizing requests and
   responses across OpenAI, Anthropic, Ollama, Groq. Callers use one standard
   format; the gateway translates to each provider's native API and back.
2. **1.2 Authentication and routing** — every request carries a team API key.
   Gateway validates the key, loads team config (allowed models, allowed
   providers), and routes to the appropriate provider.
3. **1.3 Streaming passthrough** — support streaming and non-streaming. For
   streaming the gateway forwards chunks in real time while simultaneously
   buffering the full response for observability logging.
4. **1.4 Request enrichment** — per-team injection of system prompts,
   compliance disclaimers, and content filters before forwarding.

## Out of scope (later phases, hook points only)

- Token-bucket rate limiting (Redis)
- Per-team budget/spend ceiling (Redis)
- Provider fallback + circuit breaker
- Full monitoring stack (Prometheus / Grafana / OTel collector compose)

## Architecture

```
app/
  main.py                    # FastAPI app, lifespan (load config, start watcher)
  config/
    schema.py                # pydantic: TeamConfig, ProviderConfig, EnrichmentConfig
    loader.py                # YAML loader + polling hot-reload (mtime thread)
    teams.yaml               # api keys → allowed_models, allowed_providers, enrichment
    providers.yaml           # provider base_url, api_key, models
  api/
    schemas.py               # unified OpenAI-compatible request/response/stream models
    routes.py                # /v1/chat/completions, /models, /health, /metrics
    deps.py                  # auth dependency: Bearer/x-api-key → TeamConfig
  providers/
    base.py                  # ProviderAdapter ABC
    openai_adapter.py
    anthropic_adapter.py
    groq_adapter.py
    ollama_adapter.py
    registry.py              # name → adapter; rebuilt on config reload
  routing/
    router.py                # team+model+provider policy → adapter
  enrich/
    enricher.py              # content_filter → system_prompt → disclaimer
  core/
    policy.py                # stub hook interface for rate-limit/budget/fallback
    hooks.py                 # no-op pre-call/post-call hook points
  observability.py           # OTel span + Prometheus counters (Phase-1 minimal)
config.example/              # sample YAML
tests/
  test_adapters.py
  test_routing.py
  test_auth.py
  test_enrichment.py
  test_streaming.py
requirements.txt
pyproject.toml
README.md
docker-compose.yml           # gateway + redis (observability stack later phase)
```

## Component design

### 1. Config schema (pydantic)

- `ProviderConfig`: `name`, `type` (`openai|anthropic|ollama|groq`),
  `base_url`, `api_key_ref`, `default_headers`
- `TeamConfig`: `name`, `api_key`, `allowed_models: list[str]`,
  `allowed_providers: list[str]`, `default_provider`,
  `enrichment: EnrichmentConfig | None`
- `EnrichmentConfig`: `system_prompt: str | None`, `disclaimer: str | None`,
  `content_filter: {rules: list} | None`

Config is YAML-backed and hot-reloadable via a polling watcher (thread checks
file mtime every N seconds), chosen to keep dependencies minimal and work
cross-platform (Windows dev). On reload, config objects and the provider
registry are rebuilt.

### 2. Provider abstraction (1.1)

`ProviderAdapter` ABC:

- `translate_request(req: UnifiedChatRequest) -> dict` — provider-native body
- `complete(req)` / `stream(req)` — call provider transport
- `translate_response(raw, requested_model) -> UnifiedChatResponse`
- `translate_stream_chunk(raw) -> UnifiedStreamChunk | None`

OpenAI adapter is a passthrough (already OpenAI-compatible). Groq subclasses
OpenAI. Anthropic maps `messages` → `system` + `content` / `max_tokens`.
Ollama maps to `/api/chat` schema. `registry.py` holds `name → adapter`,
rebuilt on config reload.

### 3. Auth + routing (1.2)

- `deps.py`: read key from `Authorization: Bearer` (fallback `x-api-key`).
  Lookup team by key; missing/invalid → 401.
- `router.py`: resolve model → check `allowed_models`; resolve provider →
  check `allowed_providers`. Model not allowed → 403; provider not allowed →
  403; no valid provider → 503.
- `core/policy.py` is invoked in the pipeline but no-ops in Phase 1.

### 4. Streaming passthrough (1.3)

- `stream: true` → `StreamingResponse`. Generator iterates provider SSE, yields
  translated OpenAI SSE chunks (`data: {...}\n\n`, terminated with `[DONE]`).
- Tee: each chunk also appended to an in-memory buffer; after the stream ends
  the observability layer flushes the full response log and span. Non-streaming
  accumulates the same full response. One code path, two modes.

### 5. Request enrichment (1.4)

`enricher.enrich(unified, team)` runs ordered:

1. content filter — scan user messages against rules; match → 400/block
   (rejected before send)
2. inject `system_prompt` — prepend as system message
3. append `disclaimer` — append to system prompt message

All steps per-team and optional. No enrichment configured → request passes
through unchanged.

### 6. Observability (Phase-1 minimal)

- OTel span per request (long-lived span for streaming)
- Prometheus counters: request total (by team/provider), status, latency
  histogram, token usage
- `/metrics` endpoint; Prom/Grafana/collector compose deferred to later phase

## Data flow

```
client → POST /v1/chat/completions (Bearer key)
  → deps: auth → TeamConfig
  → enricher: filter / inject / append
  → router: model+provider policy → adapter (403/503 on violation)
  → core.policy hook (no-op phase 1)
  → adapter.translate_request → provider call
  → (stream) tee chunks → translated SSE → client; buffer for logging
  → adapter.translate_response → unified → observability + respond
```

## Error handling

- 401 unknown/invalid key
- 403 model or provider not allowed
- 400 invalid body / blocked by content filter
- 503 provider unavailable / no valid provider (fail-safe, no panic)
- Upstream errors normalized to unified OpenAI-style error JSON; never leak
  provider internals

## Testing

- `pytest` + `httpx` AsyncClient + `respx` (mock HTTP) + `pytest-asyncio`
- Round-trip translate tests per adapter (provider-native payload fixtures)
- Routing / auth / enrichment unit tests
- Streaming SSE passthrough + buffer-flush test

## Build order

1. Bootstrap: pyproject, requirements, config schema + loader + sample YAML
2. Provider ABC + OpenAI/Groq adapters (passthrough) + registry
3. Anthropic + Ollama adapters (translate)
4. Auth dependency + router
5. Enrichment module
6. Streaming passthrough + logging tee
7. Observability instrumentation + `/metrics` + `/health` + `/models`
8. Hot-reload polling watcher
9. Tests + README + docker-compose (gateway + redis)

## Dependencies (locked from tech-stack.md)

`fastapi`, `uvicorn[standard]`, `httpx`, `redis`, `pyyaml`, `pydantic`,
`opentelemetry-sdk`, `prometheus-client`, `pytest`, `pytest-asyncio`, `respx`
