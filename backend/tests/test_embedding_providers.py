"""Tests for app.services.embedding_providers (Day 51).

Split deliberately:
  - Provider *selection* (which class `get_embedding_provider()` returns
    for a given EMBEDDING_PROVIDER, and that an unknown value fails
    clearly) is pure/fast - no network, no model load.
  - The OpenAI path is exercised through the same httpx.MockTransport
    style as tests/test_embeddings.py - the adapter is a one-line
    pass-through, so this mostly re-confirms the wiring, not the HTTP
    logic itself (already covered there).
  - The local path uses the *real* fastembed model (no mock) - it's the
    one genuinely new code path this day adds, and mocking it would only
    prove the mock works. `_local_model` is a module-level cache, so only
    the first test that touches it in this whole test run pays the
    download/load cost (~seconds to tens of seconds, one time); every
    test after that reuses the already-loaded model.
"""

from __future__ import annotations

import httpx
import pytest

from app.services import embedding_providers
from app.services.embedding_providers import (
    LocalEmbeddingProvider,
    OpenAIEmbeddingProvider,
    get_embedding_provider,
)
from app.services.embeddings import EmbeddingConfigError

LOCAL_EMBEDDING_DIMENSION = 384


def test_openai_is_the_default_provider():
    provider = get_embedding_provider()
    assert isinstance(provider, OpenAIEmbeddingProvider)


def test_local_provider_can_be_selected():
    provider = get_embedding_provider("local")
    assert isinstance(provider, LocalEmbeddingProvider)


def test_openai_selected_via_settings(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.embedding_provider", "openai")
    assert isinstance(get_embedding_provider(), OpenAIEmbeddingProvider)


def test_local_selected_via_settings(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.embedding_provider", "local")
    assert isinstance(get_embedding_provider(), LocalEmbeddingProvider)


def test_invalid_provider_configuration_fails_clearly():
    with pytest.raises(EmbeddingConfigError, match="bogus"):
        get_embedding_provider("bogus")


def test_provider_interface_shape():
    # Both implementations must satisfy the same async embed_texts/
    # embed_query shape - checked structurally (duck typing, matching
    # `EmbeddingProvider`'s `@runtime_checkable` Protocol) rather than
    # via inheritance, since neither class subclasses the Protocol.
    for provider in (OpenAIEmbeddingProvider(), LocalEmbeddingProvider()):
        assert isinstance(provider, embedding_providers.EmbeddingProvider)
        assert hasattr(provider, "embed_texts")
        assert hasattr(provider, "embed_query")


def _install_mock_transport(monkeypatch, handler):
    transport = httpx.MockTransport(handler)

    class FakeAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("app.services.embeddings.httpx.AsyncClient", FakeAsyncClient)


async def test_openai_provider_requires_api_key(monkeypatch):
    monkeypatch.setattr("app.services.embeddings.settings.openai_api_key", "")
    with pytest.raises(EmbeddingConfigError):
        await OpenAIEmbeddingProvider().embed_query("hello")


async def test_openai_provider_delegates_to_existing_implementation(monkeypatch):
    def handler(request):
        assert request.headers["Authorization"] == "Bearer sk-test"
        return httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": [1.0, 2.0, 3.0]}]},
        )

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.embeddings.settings.openai_api_key", "sk-test")

    vector = await OpenAIEmbeddingProvider().embed_query("hello")
    assert vector == [1.0, 2.0, 3.0]


# --- Local provider: real model, no mocking (see module docstring) ---


async def test_local_provider_does_not_require_openai_api_key(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.openai_api_key", "")
    vector = await LocalEmbeddingProvider().embed_query("def foo(): return 1")
    assert len(vector) == LOCAL_EMBEDDING_DIMENSION


async def test_local_embedding_output_has_expected_dimension():
    vectors = await LocalEmbeddingProvider().embed_texts(["hello world", "a second chunk"])
    assert len(vectors) == 2
    for vector in vectors:
        assert len(vector) == LOCAL_EMBEDDING_DIMENSION
        assert all(isinstance(component, float) for component in vector)


async def test_local_provider_empty_input_short_circuits_without_loading_model():
    assert await LocalEmbeddingProvider().embed_texts([]) == []


async def test_query_embedding_uses_the_selected_provider(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.embedding_provider", "local")
    vector = await get_embedding_provider().embed_query("select provider by config")
    assert len(vector) == LOCAL_EMBEDDING_DIMENSION

    monkeypatch.setattr("app.core.config.settings.embedding_provider", "openai")
    assert isinstance(get_embedding_provider(), OpenAIEmbeddingProvider)
