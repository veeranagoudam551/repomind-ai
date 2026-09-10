"""Unit tests for app.services.embeddings, mocking the OpenAI HTTP call
via httpx.MockTransport since no live OPENAI_API_KEY is configured for
this environment. See docs/architecture.md Day 14 for context.
"""

import json

import httpx
import pytest

from app.services.embeddings import (
    MAX_BATCH_SIZE,
    EmbeddingAPIError,
    EmbeddingConfigError,
    generate_embedding,
    generate_embeddings,
)


def _install_mock_transport(monkeypatch, handler):
    transport = httpx.MockTransport(handler)

    class FakeAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("app.services.embeddings.httpx.AsyncClient", FakeAsyncClient)


def _openai_response(texts: list[str]) -> dict:
    return {
        "data": [{"index": i, "embedding": [float(i), float(len(t))]} for i, t in enumerate(texts)],
        "model": "text-embedding-3-small",
    }


async def test_generate_embedding_requires_api_key(monkeypatch):
    monkeypatch.setattr("app.services.embeddings.settings.openai_api_key", "")
    with pytest.raises(EmbeddingConfigError):
        await generate_embedding("hello")


async def test_generate_embeddings_empty_list_returns_empty_without_calling_api(monkeypatch):
    def handler(request):
        raise AssertionError("should not make an HTTP call for an empty input list")

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.embeddings.settings.openai_api_key", "sk-test")

    assert await generate_embeddings([]) == []


async def test_generate_embedding_success(monkeypatch):
    def handler(request):
        assert request.headers["Authorization"] == "Bearer sk-test"
        return httpx.Response(200, json=_openai_response(["hello"]))

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.embeddings.settings.openai_api_key", "sk-test")

    embedding = await generate_embedding("hello")
    assert embedding == [0.0, 5.0]


async def test_generate_embeddings_preserves_order(monkeypatch):
    texts = ["a", "bb", "ccc"]

    def handler(request):
        payload = json.loads(request.content)
        assert payload["input"] == texts
        assert payload["model"] == "text-embedding-3-small"
        # Return out of order to prove the client sorts by index.
        response = _openai_response(texts)
        response["data"] = list(reversed(response["data"]))
        return httpx.Response(200, json=response)

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.embeddings.settings.openai_api_key", "sk-test")

    embeddings = await generate_embeddings(texts)
    assert embeddings == [[0.0, 1.0], [1.0, 2.0], [2.0, 3.0]]


async def test_generate_embeddings_splits_into_batches(monkeypatch):
    texts = [f"chunk-{i}" for i in range(MAX_BATCH_SIZE + 5)]
    call_sizes: list[int] = []

    def handler(request):
        payload = json.loads(request.content)
        call_sizes.append(len(payload["input"]))
        return httpx.Response(200, json=_openai_response(payload["input"]))

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.embeddings.settings.openai_api_key", "sk-test")

    embeddings = await generate_embeddings(texts)
    assert len(embeddings) == len(texts)
    assert call_sizes == [MAX_BATCH_SIZE, 5]


async def test_generate_embeddings_raises_on_api_error(monkeypatch):
    def handler(request):
        return httpx.Response(401, text='{"error": "invalid api key"}')

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.embeddings.settings.openai_api_key", "sk-bad")

    with pytest.raises(EmbeddingAPIError):
        await generate_embeddings(["hello"])


async def test_generate_embeddings_raises_on_connection_failure(monkeypatch):
    # Day 42: a fully unreachable OpenAI (DNS failure, connection refused,
    # timeout) raises a raw httpx.RequestError with no .status_code to
    # check - this must become an EmbeddingAPIError, not propagate raw.
    def handler(request):
        raise httpx.ConnectError("Connection refused", request=request)

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.embeddings.settings.openai_api_key", "sk-test")

    with pytest.raises(EmbeddingAPIError):
        await generate_embeddings(["hello"])
