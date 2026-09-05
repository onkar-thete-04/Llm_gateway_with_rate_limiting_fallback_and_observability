import httpx
import respx
from fastapi.testclient import TestClient

from app.main import app

TEAM_HEADERS = {"Authorization": "Bearer team_acme_secret_key"}


def test_non_streaming_roundtrip():
    with respx.mock:
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "chatcmpl-openai-1",
                    "object": "chat.completion",
                    "model": "gpt-4o",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "Hello from openai"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
                },
            )
        )
        with TestClient(app) as client:
            resp = client.post(
                "/v1/chat/completions",
                json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
                headers=TEAM_HEADERS,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["object"] == "chat.completion"
        assert data["choices"][0]["message"]["content"] == "Hello from openai"
        assert data["usage"]["total_tokens"] == 10


def test_streaming_passthrough():
    sse = (
        'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","model":"gpt-4o",'
        '"choices":[{"index":0,"delta":{"content":"Hello"},"finish_reason":null}]}\n\n'
        'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","model":"gpt-4o",'
        '"choices":[{"index":0,"delta":{"content":" world"},"finish_reason":null}]}\n\n'
        'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","model":"gpt-4o",'
        '"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
        "data: [DONE]\n\n"
    )
    with respx.mock:
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(200, text=sse)
        )
        with TestClient(app) as client:
            resp = client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-4o",
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": True,
                },
                headers=TEAM_HEADERS,
            )
        assert resp.status_code == 200
        body = resp.text
        assert "Hello" in body
        assert " world" in body
        assert "data: [DONE]" in body


def test_disallowed_model_returns_403():
    with TestClient(app) as client:
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "forbidden-model", "messages": [{"role": "user", "content": "hi"}]},
            headers=TEAM_HEADERS,
        )
        assert resp.status_code == 403


def test_content_filter_blocks_requests():
    with TestClient(app) as client:
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "here is confidential info"}],
            },
            headers=TEAM_HEADERS,
        )
        assert resp.status_code == 400
        assert resp.json()["object"] == "error"
