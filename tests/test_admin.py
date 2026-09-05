import fakeredis.aioredis
from fastapi.testclient import TestClient

import app.main as main


def patch_redis(monkeypatch):
    monkeypatch.setattr(main.redis, "from_url", lambda *a, **k: fakeredis.aioredis.FakeRedis())


ADMIN = {"Authorization": "Bearer admin-secret"}


def test_admin_disabled_without_key(monkeypatch):
    monkeypatch.delenv("LLM_GATEWAY_ADMIN_KEY", raising=False)
    patch_redis(monkeypatch)
    with TestClient(main.app) as client:
        resp = client.get("/admin/audit", headers=ADMIN)
        assert resp.status_code == 503


def test_admin_rejects_bad_key(monkeypatch):
    monkeypatch.setenv("LLM_GATEWAY_ADMIN_KEY", "admin-secret")
    patch_redis(monkeypatch)
    with TestClient(main.app) as client:
        resp = client.get("/admin/audit", headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 401


def test_set_limits_and_audit(monkeypatch):
    monkeypatch.setenv("LLM_GATEWAY_ADMIN_KEY", "admin-secret")
    patch_redis(monkeypatch)
    with TestClient(main.app) as client:
        resp = client.put(
            "/admin/teams/acme/limits",
            json={"requests_per_minute": 30, "tokens_per_minute": 50000},
            headers=ADMIN,
        )
        assert resp.status_code == 200
        assert resp.json()["limits"]["rpm"] == 30

        status = client.get("/admin/teams/acme/status", headers=ADMIN)
        assert status.status_code == 200
        assert status.json()["rate_limits"]["requests_per_minute"] == 30

        audit = client.get("/admin/audit", headers=ADMIN)
        entries = audit.json()["entries"]
        assert any(e["action"] == "set_limits" for e in entries)


def test_set_budget_and_status(monkeypatch):
    monkeypatch.setenv("LLM_GATEWAY_ADMIN_KEY", "admin-secret")
    patch_redis(monkeypatch)
    with TestClient(main.app) as client:
        resp = client.put(
            "/admin/teams/acme/budget",
            json={"amount_usd": 250.0, "window": "monthly"},
            headers=ADMIN,
        )
        assert resp.status_code == 200

        status = client.get("/admin/teams/acme/status", headers=ADMIN)
        assert status.json()["budget"]["limit_usd"] == 250.0


def test_unknown_team_404(monkeypatch):
    monkeypatch.setenv("LLM_GATEWAY_ADMIN_KEY", "admin-secret")
    patch_redis(monkeypatch)
    with TestClient(main.app) as client:
        resp = client.get("/admin/teams/nope/status", headers=ADMIN)
        assert resp.status_code == 404
