"""Unit tests for app.services.vector_store, mocking the Qdrant HTTP calls
via httpx.MockTransport since no live Qdrant server is available in this
environment. See docs/architecture.md Day 15 for context.
"""

import json

import httpx
import pytest

from app.core.config import settings
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
        assert payload["score_threshold"] == settings.search_score_threshold
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


# --- Day 51: provider-aware collection naming + dimension-mismatch guard ---


def test_collection_name_is_unchanged_for_openai_provider(monkeypatch):
    from app.core.config import settings
    from app.services.vector_store import _collection_name

    monkeypatch.setattr(settings, "embedding_provider", "openai")
    assert _collection_name() == settings.qdrant_collection_name


def test_collection_name_is_suffixed_for_local_provider(monkeypatch):
    from app.core.config import settings
    from app.services.vector_store import _collection_name

    monkeypatch.setattr(settings, "embedding_provider", "local")
    assert _collection_name() == f"{settings.qdrant_collection_name}_local"


async def test_ensure_collection_raises_clear_error_on_dimension_mismatch(monkeypatch):
    # The scenario Day 51 exists to prevent: a collection already holds
    # vectors of one dimension (e.g. OpenAI's 1536), and the active
    # provider just produced a different one (e.g. local's 384) - this
    # must never reach Qdrant's own upsert call and fail there with a
    # cryptic native error.
    def handler(request):
        assert request.method == "GET"
        return httpx.Response(
            200,
            json={"result": {"config": {"params": {"vectors": {"size": 1536}}}}},
        )

    _install_mock_transport(monkeypatch, handler)
    with pytest.raises(VectorStoreError, match="1536.*384|384.*1536"):
        await ensure_collection(384)


async def test_ensure_collection_allows_matching_dimension(monkeypatch):
    def handler(request):
        assert request.method == "GET"
        return httpx.Response(
            200,
            json={"result": {"config": {"params": {"vectors": {"size": 384}}}}},
        )

    _install_mock_transport(monkeypatch, handler)
    await ensure_collection(384)  # must not raise


# --- Search relevance threshold (Phase 4, Part B) ---------------------------
#
# The actual filtering happens server-side in Qdrant (score_threshold in
# the request body, asserted directly above) - Qdrant never returns a hit
# scoring below it in the first place, so these exercise search()'s own
# job: sending the configured threshold correctly, and passing through
# whatever shape Qdrant's own filtering produces (a full list, a partial
# one, or none at all) without this module doing any filtering of its
# own or treating an empty result as an error.


async def test_search_uses_configured_score_threshold_value(monkeypatch):
    monkeypatch.setattr(settings, "search_score_threshold", 0.42)

    def handler(request):
        payload = json.loads(request.content)
        assert payload["score_threshold"] == 0.42
        return httpx.Response(200, json={"result": []})

    _install_mock_transport(monkeypatch, handler)
    await search([0.1], "repo-1")


async def test_search_returns_relevant_result_above_threshold(monkeypatch):
    # A relevant-shaped result, scored well above the default 0.25 - the
    # observed "genuinely relevant" range from the live QA pass (~0.31-0.39).
    def handler(request):
        return httpx.Response(
            200,
            json={
                "result": [
                    {"id": "abc", "score": 0.35, "payload": {"code_chunk_id": "c1"}}
                ]
            },
        )

    _install_mock_transport(monkeypatch, handler)
    hits = await search([0.1], "repo-1")
    assert len(hits) == 1
    assert hits[0]["score"] == 0.35


async def test_search_all_results_below_threshold_returns_empty_list(monkeypatch):
    # What Qdrant itself returns for a query where nothing clears
    # score_threshold - the observed "nonsense query" shape (~0.20-0.22),
    # below the default 0.25. No exception, no fabricated result - a
    # clean empty list, the same shape an empty/missing collection
    # already produces (test_search_returns_empty_list_when_collection_missing
    # above), so every existing "no results" caller already handles it.
    def handler(request):
        return httpx.Response(200, json={"result": []})

    _install_mock_transport(monkeypatch, handler)
    hits = await search([0.1], "repo-1")
    assert hits == []


async def test_search_mixed_scores_only_qualifying_results_pass_through(monkeypatch):
    # Simulates what Qdrant's own score_threshold filtering produces for a
    # query with some, but not all, relevant chunks: only the hits that
    # already cleared the bar are ever in the response body at all - this
    # confirms search() doesn't second-guess or re-filter Qdrant's own
    # result set (e.g. accidentally dropping a qualifying low-but-passing
    # score, or mis-ordering it).
    def handler(request):
        return httpx.Response(
            200,
            json={
                "result": [
                    {"id": "a", "score": 0.39, "payload": {"code_chunk_id": "c1"}},
                    {"id": "b", "score": 0.31, "payload": {"code_chunk_id": "c2"}},
                    {"id": "c", "score": 0.26, "payload": {"code_chunk_id": "c3"}},
                ]
            },
        )

    _install_mock_transport(monkeypatch, handler)
    hits = await search([0.1], "repo-1")
    assert [h["id"] for h in hits] == ["a", "b", "c"]
    assert all(h["score"] >= settings.search_score_threshold for h in hits)


async def test_search_respects_limit_among_qualifying_results(monkeypatch):
    # Existing top-K behavior is preserved for results that pass the
    # threshold - limit and score_threshold are independent constraints
    # Qdrant applies together, not one replacing the other.
    def handler(request):
        payload = json.loads(request.content)
        assert payload["limit"] == 2
        assert payload["score_threshold"] == settings.search_score_threshold
        return httpx.Response(
            200,
            json={
                "result": [
                    {"id": "a", "score": 0.39, "payload": {"code_chunk_id": "c1"}},
                    {"id": "b", "score": 0.33, "payload": {"code_chunk_id": "c2"}},
                ]
            },
        )

    _install_mock_transport(monkeypatch, handler)
    hits = await search([0.1], "repo-1", limit=2)
    assert len(hits) == 2
