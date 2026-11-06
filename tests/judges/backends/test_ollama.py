import httpx
import pytest

from mtlj.judges.backends.ollama import OllamaBackend


def make_backend(response):
    def handler(request):
        assert request.url.path == "/api/chat"

        return httpx.Response(
            200,
            json=response,
        )

    return OllamaBackend(
        model="llama3.1:8b-instruct-q4_K_M",
        quantization="Q4_K_M",
        base_url="http://localhost:11434",
    ), handler


def test_ollama_returns_generated_content(monkeypatch):
    response_data = {
        "message": {
            "role": "assistant",
            "content": "The answer is faithful.",
        }
    }

    def handler(request):
        assert request.url.path == "/api/chat"
        return httpx.Response(200, json=response_data)

    backend = OllamaBackend(
        model="llama3.1:8b-instruct-q4_K_M",
        quantization="Q4_K_M",
    )

    transport = httpx.MockTransport(handler)

    monkeypatch.setattr(
        "mtlj.judges.backends.ollama.httpx.Client",
        lambda **kwargs: httpx.Client(
            transport=transport,
            **kwargs,
        ),
    )

    assert backend.generate("Evaluate this answer") == ("The answer is faithful.")


def test_ollama_rejects_http_errors(monkeypatch):
    def handler(request):
        return httpx.Response(500)

    backend = OllamaBackend(
        model="llama3.1:8b-instruct-q4_K_M",
        quantization="Q4_K_M",
    )

    transport = httpx.MockTransport(handler)

    monkeypatch.setattr(
        "mtlj.judges.backends.ollama.httpx.Client",
        lambda **kwargs: httpx.Client(
            transport=transport,
            **kwargs,
        ),
    )

    with pytest.raises(httpx.HTTPStatusError):
        backend.generate("Test prompt")


def test_ollama_records_model_and_quantization():
    backend = OllamaBackend(
        model="llama3.1:8b-instruct-q4_K_M",
        quantization="Q4_K_M",
    )

    metadata = backend.metadata

    assert metadata.provider == "ollama"
    assert metadata.model == "llama3.1:8b-instruct-q4_K_M"
    assert metadata.quantization == "Q4_K_M"
