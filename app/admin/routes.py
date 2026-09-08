"""Admin API: status, live adjustments, spending, audit, alerts."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.admin.audit import AuditLog
from app.admin.deps import require_admin
from app import observability
from app.admin.overrides import Overrides
from app.limits.budget import BudgetChecker, window_key

router = APIRouter(prefix="/admin", tags=["admin"])


class LimitsBody(BaseModel):
    requests_per_minute: int | None = None
    tokens_per_minute: int | None = None


class BudgetBody(BaseModel):
    amount_usd: float
    window: str = "monthly"


class AlertBody(BaseModel):
    webhook: str | None = None
    warn_at: float = 0.8


def _team_or_404(store, name: str):
    team = store.team_by_name(name)
    if team is None:
        raise HTTPException(status_code=404, detail=f"team not found: {name}")
    return team


def _effective_limits(team, limits_override) -> dict[str, Any]:
    rpm = tpm = 0
    if team.rate_limit is not None:
        rpm = team.rate_limit.requests_per_minute or 0
        tpm = team.rate_limit.tokens_per_minute or 0
    if limits_override:
        rpm = limits_override.get("rpm") or rpm
        tpm = limits_override.get("tpm") or tpm
    return {"requests_per_minute": rpm, "tokens_per_minute": tpm}


def _effective_budget(team, budget_override) -> dict[str, Any] | None:
    if budget_override:
        return budget_override
    if team.budget is None or team.budget.amount_usd is None:
        return None
    return {
        "amount_usd": team.budget.amount_usd,
        "window": team.budget.window,
        "warn_at": team.budget.warn_at,
    }


@router.get("/teams/{team}/status")
async def team_status(
    team: str, request: Request, _admin: str = Depends(require_admin)
):
    store = request.app.state.config_store
    overrides: Overrides = request.app.state.overrides
    redis = request.app.state.redis

    team_config = _team_or_404(store, team)
    limits_override = await overrides.get_limits(team)
    budget_override = await overrides.get_budget(team)
    limits = _effective_limits(team_config, limits_override)
    budget = _effective_budget(team_config, budget_override)

    buckets = {}
    for tier in ("realtime", "batch"):
        rpm = await redis.hget(f"rl:{team}:{tier}:rpm", "tokens")
        tpm = await redis.hget(f"rl:{team}:{tier}:tpm", "tokens")
        buckets[tier] = {
            "rpm_tokens_remaining": int(rpm or 0),
            "tpm_tokens_remaining": int(tpm or 0),
        }

    budget_status = None
    if budget:
        spent = await _current_spend(redis, team, budget["window"])
        limit_micro = int(round(budget["amount_usd"] * 1_000_000))
        budget_status = {
            "window": budget["window"],
            "spent_usd": round(spent / 1_000_000, 6),
            "limit_usd": budget["amount_usd"],
            "remaining_usd": round(max(0, limit_micro - spent) / 1_000_000, 6),
            "warn_at": budget["warn_at"],
        }

    return {
        "team": team,
        "rate_limits": limits,
        "bucket_state": buckets,
        "budget": budget_status,
    }


@router.put("/teams/{team}/limits")
async def set_limits(
    team: str,
    body: LimitsBody,
    request: Request,
    admin_key: str = Depends(require_admin),
):
    store = request.app.state.config_store
    overrides: Overrides = request.app.state.overrides
    audit: AuditLog = request.app.state.audit

    team_config = _team_or_404(store, team)
    before = await overrides.get_limits(team)

    rpm = body.requests_per_minute if body.requests_per_minute is not None else 0
    tpm = body.tokens_per_minute if body.tokens_per_minute is not None else 0
    await overrides.set_limits(team, rpm, tpm)
    after = await overrides.get_limits(team)

    await audit.log(admin_key, "set_limits", team, before=before, after=after)
    return {"team": team, "limits": after}


@router.put("/teams/{team}/budget")
async def set_budget(
    team: str,
    body: BudgetBody,
    request: Request,
    admin_key: str = Depends(require_admin),
):
    store = request.app.state.config_store
    overrides: Overrides = request.app.state.overrides
    audit: AuditLog = request.app.state.audit

    _team_or_404(store, team)
    before = await overrides.get_budget(team)
    await overrides.set_budget(team, body.amount_usd, body.window)
    after = await overrides.get_budget(team)
    observability.set_budget_limit(team, float(after["amount_usd"]))

    await audit.log(admin_key, "set_budget", team, before=before, after=after)
    return {"team": team, "budget": after}


@router.get("/teams/{team}/spending")
async def team_spending(
    team: str, request: Request, _admin: str = Depends(require_admin)
):
    store = request.app.state.config_store
    redis = request.app.state.redis
    _team_or_404(store, team)

    keys = await redis.keys(f"budget:{team}:*")
    history = []
    for key in keys:
        value = await redis.get(key)
        spend_micro = int(value or 0)
        history.append(
            {
                "window": key.decode().split(":", 2)[-1],
                "spent_usd": round(spend_micro / 1_000_000, 6),
            }
        )
    history.sort(key=lambda item: item["window"])
    return {"team": team, "history": history}


@router.get("/audit")
async def audit_entries(
    request: Request, limit: int = 100, _admin: str = Depends(require_admin)
):
    audit: AuditLog = request.app.state.audit
    return {"entries": await audit.entries(limit)}


@router.post("/teams/{team}/alerts")
async def set_alerts(
    team: str,
    body: AlertBody,
    request: Request,
    admin_key: str = Depends(require_admin),
):
    store = request.app.state.config_store
    overrides: Overrides = request.app.state.overrides
    audit: AuditLog = request.app.state.audit

    team_config = _team_or_404(store, team)
    budget_override = await overrides.get_budget(team)
    budget = _effective_budget(team_config, budget_override)
    if budget is None:
        raise HTTPException(status_code=409, detail="team has no budget configured")

    await overrides.set_budget(
        team,
        budget["amount_usd"],
        budget["window"],
        warn_at=body.warn_at,
        alert_webhook=body.webhook,
    )
    await audit.log(
        admin_key,
        "set_alerts",
        team,
        after={"warn_at": body.warn_at, "webhook": body.webhook},
    )
    return {"team": team, "alerts": {"warn_at": body.warn_at, "webhook": body.webhook}}


async def _current_spend(redis, team: str, window: str) -> int:
    key = f"budget:{team}:{window_key(window)}"
    value = await redis.get(key)
    return int(value or 0)
