# LLM API Gateway — Tech Stack

## Language & Framework
- **Python 3.11+**
- **FastAPI** (async-native) — gateway service, OpenAI-compatible chat completions endpoint

## Rate Limiting & Budget
- **Redis** — distributed, sub-ms enforcement
- **Token-bucket algorithm** — separate `burst_capacity` and `refill_rate` per team
- **Redis-backed budget tracking** — per-team spend ceiling, checked alongside rate limit

## Config
- **YAML** — rate limits, budgets, provider routing/fallback order, circuit-breaker thresholds
- Hot-reloadable without redeploy (reload mechanism — file-watcher vs polling — is a stop-and-ask decision, not yet locked)

## Providers (pluggable, common interface)
- **OpenAI**
- **Anthropic**
- **Ollama** (local, $0 cost)
- **Groq** (OpenAI-compatible endpoint, free tier, fallback/budget-tier role)

## Resilience
- Retry with exponential backoff + jitter (max 2 retries on transient errors)
- Per-provider circuit breaker (opens after 3 consecutive failures, 30s cooldown, half-open probe)
- Fail-closed on Redis unavailability (503, not bypass)

## Observability
- **OpenTelemetry** — distributed tracing (span per request; long-lived span for streaming requests)
- **Prometheus** — metrics: latency, token usage, cost, success/failure, provider used, fallback count
- **Grafana** — dashboards: per-team volume, spend, error rate, fallback frequency

## Orchestration
- **Docker + docker-compose** — gateway + Redis + Prometheus + Grafana + OTel collector, single-command startup, local/prod parity

## Dependencies (locked, no substitutions without asking)
- `redis-py`
- `opentelemetry-sdk`
- `prometheus-client`
