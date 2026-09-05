"""RateLimitPolicy: the Phase-2 policy that replaces the Phase-1 no-op.

Enforces rate limits and budget before forwarding, records spend after the
provider responds. Raises ``RateLimitError`` / ``BudgetError`` / ``PolicyError``
which the route layer maps to 429 / 429 / 503 responses.
"""
from __future__ import annotations

from typing import Any

from app.api.schemas import UnifiedChatRequest
from app.config.schema import TeamConfig
from app.core.policy import PolicyError
from app.limits.budget import BudgetChecker, BudgetError
from app.limits.rate_limiter import RateLimitError, RateLimiter, estimate_input_tokens
from app.limits.pricing import PricingTable

REALTIME_SHARE = 0.7
BATCH_SHARE = 0.3
_MICRO_PER_USD = 1_000_000


class RateLimitPolicy:
    def __init__(
        self,
        rate_limiter: RateLimiter,
        budget_checker: BudgetChecker,
        pricing: PricingTable,
        overrides=None,
        alert_manager=None,
    ) -> None:
        self._rate_limiter = rate_limiter
        self._budget = budget_checker
        self._pricing = pricing
        self._overrides = overrides
        self._alerts = alert_manager

    async def check(self, team: TeamConfig, req: UnifiedChatRequest) -> None:
        tier = getattr(req, "priority", "batch") or "batch"
        input_tokens = estimate_input_tokens(req)
        try:
            rpm, tpm = await self._effective_limits(team)
            tier_rpm, tier_tpm = self._tier_limits(team, tier, rpm, tpm)
            await self._rate_limiter.check(team.name, tier, tier_rpm, tier_tpm, input_tokens)
            await self._check_budget(team, req)
        except (RateLimitError, BudgetError):
            raise
        except Exception as exc:  # noqa: BLE001 - fail closed on Redis problems
            raise PolicyError("rate limiting unavailable", status_code=503) from exc

    async def record(self, team: TeamConfig, req: UnifiedChatRequest, usage: Any) -> None:
        prompt_tokens = int((usage or {}).get("prompt_tokens", 0) or 0)
        completion_tokens = int((usage or {}).get("completion_tokens", 0) or 0)
        try:
            budget_cfg = await self._effective_budget(team)
            if budget_cfg is None or budget_cfg["amount_usd"] is None:
                return
            new_spend = await self._budget.record(
                team.name,
                req.model,
                prompt_tokens,
                completion_tokens,
                budget_cfg["window"],
            )
        except Exception:  # noqa: BLE001 - don't fail a completed request on logging
            return
        limit_micro = int(round(budget_cfg["amount_usd"] * _MICRO_PER_USD))
        if new_spend >= limit_micro * budget_cfg["warn_at"]:
            await self._notify_alert(team, budget_cfg, new_spend, limit_micro)

    async def _effective_limits(self, team: TeamConfig) -> tuple[float, float]:
        rpm = tpm = 0.0
        if self._overrides is not None:
            limits = await self._overrides.get_limits(team.name)
            if limits:
                rpm = float(limits.get("rpm") or 0)
                tpm = float(limits.get("tpm") or 0)
        if team.rate_limit is not None:
            rpm = rpm or (team.rate_limit.requests_per_minute or 0)
            tpm = tpm or (team.rate_limit.tokens_per_minute or 0)
        return rpm, tpm

    async def _effective_budget(self, team: TeamConfig) -> dict[str, Any] | None:
        if self._overrides is not None:
            budget = await self._overrides.get_budget(team.name)
            if budget:
                return budget
        if team.budget is None or team.budget.amount_usd is None:
            return None
        return {
            "amount_usd": team.budget.amount_usd,
            "window": team.budget.window,
            "warn_at": team.budget.warn_at,
        }

    async def _check_budget(self, team: TeamConfig, req: UnifiedChatRequest) -> None:
        budget_cfg = await self._effective_budget(team)
        if budget_cfg is None or budget_cfg["amount_usd"] is None:
            return
        limit_micro = int(round(budget_cfg["amount_usd"] * _MICRO_PER_USD))
        try:
            warn = await self._budget.check(
                team.name,
                req.model,
                limit_micro,
                budget_cfg["window"],
                budget_cfg["warn_at"],
            )
        except BudgetError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise PolicyError("budget tracking unavailable", status_code=503) from exc
        if warn:
            spend = await self._budget.current_spend_micro(team.name, budget_cfg["window"])
            await self._notify_alert(team, budget_cfg, spend, limit_micro)

    async def _notify_alert(
        self, team: TeamConfig, budget_cfg: dict[str, Any], spend: int, limit: int
    ) -> None:
        if self._alerts is None:
            return
        webhook = budget_cfg.get("alert_webhook") or (
            team.budget.alert_webhook if team.budget else None
        )
        await self._alerts.notify(
            team=team.name,
            window=budget_cfg["window"],
            spend_micro=spend,
            limit_micro=limit,
            webhook=webhook,
        )

    @staticmethod
    def _tier_limits(
        team: TeamConfig, tier: str, rpm: float, tpm: float
    ) -> tuple[float, float]:
        tiers = (team.rate_limit.tiers if team.rate_limit else {}) or {}
        tier_cfg = tiers.get(tier)
        if tier_cfg is not None and (
            tier_cfg.requests_per_minute is not None or tier_cfg.tokens_per_minute is not None
        ):
            return (
                float(tier_cfg.requests_per_minute or 0),
                float(tier_cfg.tokens_per_minute or 0),
            )
        share = REALTIME_SHARE if tier == "realtime" else BATCH_SHARE
        return rpm * share, tpm * share
