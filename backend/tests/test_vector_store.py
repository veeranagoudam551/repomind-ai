"""Unit tests for app.services.vector_store, mocking the Qdrant HTTP calls
via httpx.MockTransport since no live Qdrant server is available in this
environment. See docs/architecture.md Day 15 for context.
"""

import json

import httpx
import pytest

from app.services.vector_store import (
    VectorStoreError,
    delete_by_repository,
    ensure_collection,
    search,
    upsert_chunks,
)


def _install_mock_transport(monkeypatch, handler):
    transport = httpx.MockTransport(handler)

    class FakeAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("app.services.vector_store.httpx.AsyncClient", FakeAsyncClient)


async def test_ensure_collection_skips_creation_when_it_already_exists(monkeypatch):
    def handler(request):
        assert request.method == "GET"
        return httpx.Response(200, json={"result": {}})

    _install_mock_transport(monkeypatch, handler)
    await ensure_collection(1536)


async def test_ensure_collection_creates_when_missing(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request.method)
        if request.method == "GET":
            return httpx.Response(404, json={"status": "not found"})
        payload = json.loads(request.content)
        assert payload == {"vectors": {"size": 1536, "distance": "Cosine"}}
        return httpx.Response(200, json={"result": True})

    _install_mock_transport(monkeypatch, handler)
    await ensure_collection(1536)
    assert calls == ["GET", "PUT"]


async def test_ensure_collection_raises_on_unexpected_get_status(monkeypatch):
    def handler(request):
        return httpx.Response(500, text="boom")

    _install_mock_transport(monkeypatch, handler)
    with pytest.raises(VectorStoreError):
        await ensure_collection(1536)


async def test_ensure_collection_raises_on_create_failure(monkeypatch):
    def handler(request):
        if request.method == "GET":
            return httpx.Response(404, json={})
        return httpx.Response(400, text="bad request")

    _install_mock_transport(monkeypatch, handler)
    with pytest.raises(VectorStoreError):
        await ensure_collection(1536)


async def test_ensure_collection_raises_on_connection_failure(monkeypatch):
    # Day 42: a fully unreachable Qdrant (as opposed to Qdrant responding
    # with an error status) raises a raw httpx.RequestError, not something
    # with a .status_code - this must become a VectorStoreError like any
    # other failure, not propagate as an unhandled exception.
    def handler(request):
        raise httpx.ConnectError("Connection refused", request=request)

    _install_mock_transport(monkeypatch, handler)
    with pytest.raises(VectorStoreError):
        await ensure_collection(1536)


async def test_upsert_chunks_empty_list_short_circuits(monkeypatch):
    def handler(request):
        raise AssertionError("should not make an HTTP call for an empty point list")

    _install_mock_transport(monkeypatch, handler)
    await upsert_chunks([])


async def test_upsert_chunks_ensures_collection_then_upserts(monkeypatch):
    calls = []

    def handler(request):
        calls.append((request.method, str(request.url)))
        if request.method == "GET":
            return httpx.Response(200, json={"result": {}})
        payload = json.loads(request.content)
        assert payload["points"][0]["id"] == "abc"
        assert payload["points"][0]["vector"] == [0.1, 0.2]
        return httpx.Response(200, json={"result": {}})

    _install_mock_transport(monkeypatch, handler)
    await upsert_chunks(
        [{"id": "abc", "vector": [0.1, 0.2], "payload": {"repository_id": "r1"}}]
    )
    assert [c[0] for c in calls] == ["GET", "PUT"]
    assert calls[1][1].endswith("/points?wait=true")


async def test_upsert_chunks_raises_on_api_error(monkeypatch):
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"result": {}})
        return httpx.Response(500, text="boom")

    _install_mock_transport(monkeypatch, handler)
    with pytest.raises(VectorStoreError):
        await upsert_chunks([{"id": "abc", "vector": [0.1], "payload": {}}])


async def test_upsert_chunks_raises_on_connection_failure(monkeypatch):
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"result": {}})
        raise httpx.ConnectTimeout("timed out", request=request)

    _install_mock_transport(monkeypatch, handler)
    with pytest.raises(VectorStoreError):
        await upsert_chunks([{"id": "abc", "vector": [0.1], "payload": {}}])


async def test_search_sends_vector_and_repository_filter(monkeypatch):
    def handler(request):
        assert request.url.path.endswith("/points/search")
        payload = json.loads(request.content)
        assert payload["vector"] == [0.1, 0.2]
        assert payload["limit"] == 5
        assert payload["filter"] == {
            "must": [{"key": "repository_id", "match": {"value": "repo-1"}}]
        }
        return httpx.Response(
            200,
            json={"result": [{"id": "abc", "score": 0.9, "payload": {"code_chunk_id": "c1"}}]},
        )

    _install_mock_transport(monkeypatch, handler)
    hits = await search([0.1, 0.2], "repo-1", limit=5)
    assert hits == [{"id": "abc", "score": 0.9, "payload": {"code_chunk_id": "c1"}}]


async def test_search_returns_empty_list_when_collection_missing(monkeypatch):
    def handler(request):
        return httpx.Response(404, json={"status": "not found"})

    _install_mock_transport(monkeypatch, handler)
    assert await search([0.1], "repo-1") == []


async def test_search_raises_on_api_error(monkeypatch):
    def handler(request):
        return httpx.Response(500, text="boom")

    _install_mock_transport(monkeypatch, handler)
    with pytest.raises(VectorStoreError):
        await search([0.1], "repo-1")


async def test_search_raises_on_connection_failure(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("Connection refused", request=request)

    _install_mock_transport(monkeypatch, handler)
    with pytest.raises(VectorStoreError):
        await search([0.1], "repo-1")


async def test_delete_by_repository_sends_payload_filter(monkeypatch):
    def handler(request):
        assert request.url.path.endswith("/points/delete")
        payload = json.loads(request.content)
        assert payload == {
            "filter": {
                "must": [{"key": "repository_id", "match": {"value": "11111111-1111-1111-1111-111111111111"}}]
            }
        }
        return httpx.Response(200, json={"result": {}})

    _install_mock_transport(monkeypatch, handler)
    await delete_by_repository("11111111-1111-1111-1111-111111111111")


async def test_delete_by_repository_is_noop_when_collection_missing(monkeypatch):
    def handler(request):
        return httpx.Response(404, json={"status": "not found"})

    _install_mock_transport(monkeypatch, handler)
    await delete_by_repository("11111111-1111-1111-1111-111111111111")


async def test_delete_by_repository_raises_on_api_error(monkeypatch):
    def handler(request):
        return httpx.Response(500, text="boom")

    _install_mock_transport(monkeypatch, handler)
    with pytest.raises(VectorStoreError):
        await delete_by_repository("11111111-1111-1111-1111-111111111111")


async def test_delete_by_repository_raises_on_connection_failure(monkeypatch):
    # This is the exact case Day 39/41 found live: an unreachable Qdrant
    # used to bubble up as a raw httpx.ConnectError past delete_repository's
    # `except VectorStoreError` in app/api/repositories.py, surfacing as an
    # unhandled 500 instead of the graceful 502 every other failure gets.
    def handler(request):
        raise httpx.ConnectError("Connection refused", request=request)

    _install_mock_transport(monkeypatch, handler)
    with pytest.raises(VectorStoreError):
        await delete_by_repository("11111111-1111-1111-1111-111111111111")
