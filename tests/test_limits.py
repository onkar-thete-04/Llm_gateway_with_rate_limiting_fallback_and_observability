from fastapi.testclient import TestClient

import app.main as main

TEAM_HEADERS = {"Authorization": "Bearer team_acme_secret_key"}
CHAT = {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}


def _write_config(tmp_path, teams_yaml: str, providers_yaml: str):
    (tmp_path / "teams.yaml").write_text(teams_yaml, encoding="utf-8")
    (tmp_path / "providers.yaml").write_text(providers_yaml, encoding="utf-8")


PROVIDERS = """
providers:
  - name: openai
    type: openai
    base_url: https://api.openai.com/v1
    api_key_ref: OPENAI_API_KEY
    models: [gpt-4o]
"""


def test_rate_limit_returns_429_with_retry_after(tmp_path, monkeypatch):
    teams = """
teams:
  - name: acme
    api_key: team_acme_secret_key
    allowed_models: [gpt-4o]
    allowed_providers: [openai]
    rate_limit:
      requests_per_minute: 1
      tokens_per_minute: 10000
"""
    _write_config(tmp_path, teams, PROVIDERS)
    monkeypatch.setattr(main, "CONFIG_DIR", str(tmp_path))
    with TestClient(main.app) as client:
        resp = client.post("/v1/chat/completions", json=CHAT, headers=TEAM_HEADERS)
        assert resp.status_code == 429
        assert resp.json()["type"] == "rate_limit"
        assert int(resp.headers["Retry-After"]) >= 1


def test_budget_exceeded_returns_429(tmp_path, monkeypatch):
    teams = """
teams:
  - name: acme
    api_key: team_acme_secret_key
    allowed_models: [gpt-4o]
    allowed_providers: [openai]
    budget:
      amount_usd: 0.0
      window: monthly
"""
    _write_config(tmp_path, teams, PROVIDERS)
    monkeypatch.setattr(main, "CONFIG_DIR", str(tmp_path))
    with TestClient(main.app) as client:
        resp = client.post("/v1/chat/completions", json=CHAT, headers=TEAM_HEADERS)
        assert resp.status_code == 429
        assert resp.json()["type"] == "budget_exceeded"
