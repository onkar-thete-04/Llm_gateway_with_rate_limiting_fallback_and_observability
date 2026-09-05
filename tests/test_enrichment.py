import pytest

from app.api.schemas import UnifiedChatRequest, UnifiedMessage
from app.config.schema import ContentFilterConfig, ContentFilterRule, EnrichmentConfig, TeamConfig
from app.enrich.enricher import EnrichmentBlockError, enrich


def team_with(enrichment):
    return TeamConfig(
        name="t",
        api_key="k",
        allowed_models=[],
        allowed_providers=[],
        enrichment=enrichment,
    )


def make_request():
    return UnifiedChatRequest(
        model="m",
        messages=[UnifiedMessage(role="user", content="tell me about confidential data")],
    )


def test_enrich_injects_system_prompt_then_disclaimer():
    team = team_with(
        EnrichmentConfig(system_prompt="be brief", disclaimer="internal use only")
    )
    req = make_request()
    enrich(req, team)
    assert req.messages[0].role == "system"
    assert req.messages[0].content == "be brief\n\ninternal use only"
    assert req.messages[1].role == "user"


def test_enrich_disclaimer_without_system_prompt():
    team = team_with(EnrichmentConfig(disclaimer="internal only"))
    req = make_request()
    enrich(req, team)
    assert req.messages[0].role == "system"
    assert req.messages[0].content == "internal only"


def test_enrich_content_filter_blocks():
    team = team_with(
        EnrichmentConfig(
            content_filter=ContentFilterConfig(
                rules=[ContentFilterRule(pattern="confidential", block=True)]
            )
        )
    )
    with pytest.raises(EnrichmentBlockError):
        enrich(make_request(), team)


def test_enrich_no_config_passes_through():
    req = make_request()
    enrich(req, team_with(None))
    assert len(req.messages) == 1
