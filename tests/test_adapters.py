from app.api.schemas import UnifiedChatRequest, UnifiedMessage
from app.providers.anthropic_adapter import AnthropicAdapter
from app.providers.groq_adapter import GroqAdapter
from app.providers.ollama_adapter import OllamaAdapter
from app.providers.openai_adapter import OpenAIAdapter


def make_request(**overrides):
    defaults = {
        "model": "gpt-4o",
        "messages": [UnifiedMessage(role="user", content="hello")],
        "stream": False,
        "temperature": 0.7,
        "max_tokens": 100,
    }
    defaults.update(overrides)
    return UnifiedChatRequest(**defaults)


def test_openai_translate_request_maps_fields(client, secrets, openai_provider):
    adapter = OpenAIAdapter(openai_provider, client, secrets)
    body = adapter.translate_request(make_request(top_p=0.9, stop=["END"]))
    assert body["model"] == "gpt-4o"
    assert body["messages"][0] == {"role": "user", "content": "hello"}
    assert body["temperature"] == 0.7
    assert body["max_tokens"] == 100
    assert body["top_p"] == 0.9
    assert body["stop"] == ["END"]


def test_openai_translate_response(client, secrets, openai_provider):
    adapter = OpenAIAdapter(openai_provider, client, secrets)
    raw = {
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
    }
    resp = adapter.translate_response(raw, "gpt-4o", "id-1")
    assert resp.model == "gpt-4o"
    assert resp.id == "id-1"
    assert resp.choices[0].message.content == "hi"
    assert resp.usage.prompt_tokens == 5
    assert resp.usage.completion_tokens == 2
    assert resp.usage.total_tokens == 7


def test_groq_is_openai_subclass(client, secrets):
    from app.config.schema import ProviderConfig

    groq = ProviderConfig(name="groq", type="groq", base_url="https://api.groq.com/openai/v1")
    adapter = GroqAdapter(groq, client, secrets)
    assert adapter.provider_type == "groq"
    body = adapter.translate_request(make_request(model="groq-llama3"))
    assert body["model"] == "groq-llama3"


def test_anthropic_translate_request(client, secrets, anthropic_provider):
    adapter = AnthropicAdapter(anthropic_provider, client, secrets)
    req = make_request(
        model="claude-3-5-sonnet",
        messages=[
            UnifiedMessage(role="system", content="be nice"),
            UnifiedMessage(role="user", content="hello"),
        ],
        max_tokens=None,
    )
    body = adapter.translate_request(req)
    assert body["system"] == "be nice"
    assert body["messages"] == [{"role": "user", "content": "hello"}]
    assert body["max_tokens"] == 1024


def test_anthropic_translate_response(client, secrets, anthropic_provider):
    adapter = AnthropicAdapter(anthropic_provider, client, secrets)
    raw = {
        "content": [{"type": "text", "text": "Hello"}, {"type": "text", "text": " world"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 3, "output_tokens": 4},
    }
    resp = adapter.translate_response(raw, "claude-3-5-sonnet", "id-2")
    assert resp.choices[0].message.content == "Hello world"
    assert resp.choices[0].finish_reason == "stop"
    assert resp.usage.prompt_tokens == 3
    assert resp.usage.completion_tokens == 4


def test_anthropic_stream_chunk_delta(client, secrets, anthropic_provider):
    adapter = AnthropicAdapter(anthropic_provider, client, secrets)
    chunk = adapter.translate_stream_chunk(
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hi"}},
        "claude-3-5-sonnet",
        "id-3",
    )
    assert chunk.choices[0].message.content == "Hi"
    assert chunk.choices[0].finish_reason is None


def test_anthropic_stream_chunk_finish(client, secrets, anthropic_provider):
    adapter = AnthropicAdapter(anthropic_provider, client, secrets)
    chunk = adapter.translate_stream_chunk(
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
        "claude-3-5-sonnet",
        "id-3",
    )
    assert chunk.choices[0].finish_reason == "stop"


def test_ollama_translate_request(client, secrets, ollama_provider):
    adapter = OllamaAdapter(ollama_provider, client, secrets)
    body = adapter.translate_request(make_request(model="llama3.1", max_tokens=50))
    assert body["model"] == "llama3.1"
    assert body["options"]["num_predict"] == 50


def test_ollama_translate_response(client, secrets, ollama_provider):
    adapter = OllamaAdapter(ollama_provider, client, secrets)
    raw = {
        "message": {"role": "assistant", "content": "yo"},
        "done": True,
        "prompt_eval_count": 10,
        "eval_count": 3,
    }
    resp = adapter.translate_response(raw, "llama3.1", "id-4")
    assert resp.choices[0].message.content == "yo"
    assert resp.usage.prompt_tokens == 10
    assert resp.usage.completion_tokens == 3


def test_ollama_stream_done_chunk(client, secrets, ollama_provider):
    adapter = OllamaAdapter(ollama_provider, client, secrets)
    chunk = adapter.translate_stream_chunk(
        {"message": {"role": "assistant", "content": ""}, "done": True},
        "llama3.1",
        "id-5",
    )
    assert chunk.choices[0].finish_reason == "stop"
