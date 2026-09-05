# Phase 2 — Rate Limiting & Budget Enforcement (Design)

Date: 2026-09-05
Status: Approved

## Goal

Enforce per-team rate limits (requests/minute and tokens/minute) and dollar
budget caps using Redis, atomically and distributed-safe. Add tiered priority
(realtime vs batch) and an admin API for live adjustments with a full audit
log.

## Scope

1. **2.1 Token bucket rate limiting** — Redis token buckets for RPM and TPM.
   Enforce before forwarding. `429` + `Retry-After` when hit. Atomic and
   distributed-safe.
2. **2.2 Budget caps** — monthly/daily USD budget. Cost =
   `input_tokens * input_price + output_tokens * output_price`. Warn at 80%,
   block at 100% with a clear error.
3. **2.3 Tiered rate limits** — requests declare `priority: realtime|batch`.
   Realtime reserves 70% of capacity, batch 30%. Default is `batch`.
4. **2.4 Admin API** — view status, adjust limits/budgets without restart,
   spending dashboard, alerts, and a logged audit trail (who/when).

## Decisions (locked)

- Two buckets per team per tier: RPM + TPM, both token-bucket via one Lua
  script executed atomically in Redis.
- Input tokens enforced pre-call using a `len/4` approximation (placeholder —
  swap for a real tokenizer before production budget enforcement).
- Output tokens recorded post-call (real provider usage) and throttle the
  next request.
- Pricing in a separate `pricing.yaml`, per model, prices per 1M tokens.
- Budget window: `monthly | daily` per team. Spend stored as integer
  micro-dollars to avoid float drift.
- Warn threshold 80% (header + alert event, request still served); block at
  100% (`429 budget_exceeded`).
- Fail-closed: Redis unreachable → `503`, never bypass.
- Admin auth via `LLM_GATEWAY_ADMIN_KEY`. Overrides stored in Redis
  (`appendonly yes` persistence), merged over base config at check time.
- Audit log: every admin mutation appends `{ts, actor, action, team, before,
  after}` to `admin:audit`.

## Architecture

```
app/
  limits/
    __init__.py
    pricing.py          # load pricing.yaml → per-model prices
    bucket.py           # Redis token-bucket Lua script (atomic)
    rate_limiter.py     # RPM+TPM × priority tier, 429 + Retry-After
    budget.py           # spend tracking, warn threshold, block
    policy.py           # RateLimitPolicy (implements PolicyHook)
  admin/
    __init__.py
    deps.py             # LLM_GATEWAY_ADMIN_KEY auth
    audit.py            # append audit entries
    overrides.py        # Redis admin overrides
    routes.py           # /admin/* endpoints
pricing.yaml
```

## Component design

### Token bucket (`bucket.py`)

Lua script operating on a Redis hash `{tokens, ts}`:

1. refill = `min(capacity, tokens + (now - ts) * refill_rate)`
2. if refill < requested → return `{0, refill, retry_after}`
3. else decrement, update timestamp, set TTL, return `{1, remaining}`

Single script → atomic, race-free across workers.

### Rate limiter (`rate_limiter.py`)

- Resolves effective team limit = admin override (Redis) else team config.
- Splits capacity by priority: realtime 70%, batch 30% (or per-tier override).
- Checks RPM bucket then TPM bucket. Denied → `RateLimitError(retry_after)`.
- TPM input estimate = `sum(len(content) // 4)` over messages.

### Budget (`budget.py`)

- `pricing.py` loads `pricing.yaml`: `model → {input_price_per_1m,
  output_price_per_1m}`.
- Cost = `prompt_tokens/1e6*ip + completion_tokens/1e6*op`.
- Window key: `budget:{team}:{YYYY-MM}` or `{YYYY-MM-DD}`.
- Atomic Lua increment: if new spend <= limit → record; else reject.
- Crossing 80% fires alert (webhook if configured) once per window.

### Policy (`policy.py`)

`RateLimitPolicy` implements the `PolicyHook` protocol from Phase 1:

- `check(team, req)` — rate limit (429 + Retry-After) then budget (429
  budget_exceeded). Redis error → `PolicyError(503)`.
- `record(team, req, usage)` — persist real token usage + cost post-call.

Wired into `main.py` lifespan, replacing `NoopPolicy`. A `redis.asyncio`
client is created from `REDIS_URL` (default `redis://localhost:6379`).

### Admin API

- `GET  /admin/teams/{team}/status`
- `PUT  /admin/teams/{team}/limits`
- `PUT  /admin/teams/{team}/budget`
- `GET  /admin/teams/{team}/spending`
- `GET  /admin/audit`
- `POST /admin/teams/{team}/alerts`

## Pipeline

```
auth → enrich → policy.check(team, req)   # RPM+TPM → budget
     → route → provider call
     → policy.record(team, req, usage)    # cost + spend
```

Streaming: `check` before stream starts; `record` in `finally` after stream
ends.

## Testing

- `fakeredis` (dev-only) for bucket/budget/override/audit unit tests.
- Bucket refill/deny/retry_after behavior.
- Budget boundaries (79/80/100%).
- Priority split (70/30).
- Admin auth, override merge, audit entries.
- Route-level 429 + Retry-After and budget 429.
- Fail-closed 503 when Redis unavailable.
