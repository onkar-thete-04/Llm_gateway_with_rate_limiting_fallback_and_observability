"""Per-team request enrichment.

Runs in order:

1. content filter — reject the request if any user message matches a blocking
   rule (substring match).
2. system prompt injection — prepend the team's system prompt as a system
   message.
3. disclaimer — append the team's compliance disclaimer to the system prompt.

All steps are optional and driven by the team's ``enrichment`` config.
"""
from __future__ import annotations

from app.api.schemas import UnifiedChatRequest, UnifiedMessage
from app.config.schema import TeamConfig


class EnrichmentBlockError(Exception):
    """Raised when a content filter blocks the request."""


def enrich(req: UnifiedChatRequest, team: TeamConfig) -> UnifiedChatRequest:
    enrichment = team.enrichment
    if enrichment is None:
        return req

    _apply_content_filter(req, enrichment.content_filter)
    _inject_system_prompt(req, enrichment.system_prompt)
    _append_disclaimer(req, enrichment.disclaimer)
    return req


def _apply_content_filter(req: UnifiedChatRequest, content_filter) -> None:
    if content_filter is None or not content_filter.rules:
        return
    for message in req.messages:
        if message.role != "user" or not message.content:
            continue
        for rule in content_filter.rules:
            if rule.block and rule.pattern in message.content:
                raise EnrichmentBlockError(
                    f"request blocked by content filter: '{rule.pattern}'"
                )


def _inject_system_prompt(req: UnifiedChatRequest, system_prompt: str | None) -> None:
    if not system_prompt:
        return
    req.messages.insert(0, UnifiedMessage(role="system", content=system_prompt))


def _append_disclaimer(req: UnifiedChatRequest, disclaimer: str | None) -> None:
    if not disclaimer:
        return
    for message in req.messages:
        if message.role == "system":
            message.content = f"{message.content}\n\n{disclaimer}"
            return
    req.messages.insert(0, UnifiedMessage(role="system", content=disclaimer))
