"""Rate limiting: RPM + TPM per team per priority tier.

Input tokens are estimated pre-call with a ``len/4`` heuristic (placeholder —
swap for a real tokenizer before production budget enforcement). Output tokens
are recorded post-call and throttle subsequent requests.
"""
from __future__ import annotations

from app.api.schemas import UnifiedChatRequest
from app.limits.bucket import BucketResult, TokenBucket


class RateLimitError(Exception):
    def __init__(self, message: str, retry_after: int) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def estimate_input_tokens(req: UnifiedChatRequest) -> int:
    total = 0
    for message in req.messages:
        content = message.content or ""
        total += max(1, len(content) // 4)
    return total


class RateLimiter:
    def __init__(self, bucket: TokenBucket) -> None:
        self._bucket = bucket

    async def check(
        self,
        team_name: str,
        tier: str,
        tier_rpm: float,
        tier_tpm: float,
        input_tokens: int,
    ) -> None:
        if tier_rpm and tier_rpm > 0:
            result = await self._bucket.take(
                key=f"rl:{team_name}:{tier}:rpm",
                capacity=tier_rpm,
                refill_rate=tier_rpm / 60.0,
                requested=1.0,
            )
            self._raise_if_denied(result, "request rate limit exceeded")

        if tier_tpm and tier_tpm > 0:
            result = await self._bucket.take(
                key=f"rl:{team_name}:{tier}:tpm",
                capacity=tier_tpm,
                refill_rate=tier_tpm / 60.0,
                requested=float(max(1, input_tokens)),
            )
            self._raise_if_denied(result, "token rate limit exceeded")

    @staticmethod
    def _raise_if_denied(result: BucketResult, message: str) -> None:
        if not result.allowed:
            raise RateLimitError(message, retry_after=max(1, result.retry_after))
