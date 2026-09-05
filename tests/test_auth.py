from fastapi.testclient import TestClient

from app.main import app


def test_missing_key_returns_401():
    with TestClient(app) as client:
        resp = client.get("/models")
        assert resp.status_code == 401


def test_invalid_key_returns_401():
    with TestClient(app) as client:
        resp = client.get("/models", headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 401


def test_valid_key_lists_models():
    with TestClient(app) as client:
        resp = client.get(
            "/models", headers={"Authorization": "Bearer team_acme_secret_key"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["object"] == "list"
        ids = [m["id"] for m in data["data"]]
        assert "gpt-4o" in ids


def test_x_api_key_header_accepted():
    with TestClient(app) as client:
        resp = client.get("/models", headers={"x-api-key": "team_beta_secret_key"})
        assert resp.status_code == 200
