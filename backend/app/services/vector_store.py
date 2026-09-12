"""Qdrant vector store client (architecture.md Phase 2/3 - vector storage).

Thin REST wrapper over Qdrant's HTTP API, mirroring the style of
app/services/github.py and app/services/embeddings.py (a plain httpx call
per operation) rather than pulling in the `qdrant-client` SDK for the three
operations the ingestion pipeline needs: create-collection-if-missing,
upsert points, and delete-by-filter.

Point IDs are the `code_chunks.id` UUID itself, so once a chunk is embedded,
`code_chunks.vector_id` (added Day 8) is always just `str(chunk.id)` - no
separate ID scheme to keep in sync between Postgres and Qdrant.

Day 51: the collection name is provider-aware (`_collection_name()`), not
just `settings.qdrant_collection_name` verbatim - different embedding
providers (OpenAI vs. the new local one) produce vectors of different,
mutually incompatible dimensions, and Qdrant collections have one fixed
dimension for their lifetime. Mixing them in one collection would either
hard-fail every upsert after the first (Qdrant enforces this) or, worse,
be silently confusing about *why*. See `_collection_name()` and
`ensure_collection()`'s explicit dimension check below for the two-layer
guard against that.
"""

from __future__ import annotations

import uuid
from typing import Optional

import httpx

from app.core.config import settings

DISTANCE_METRIC = "Cosine"

# "openai" gets no suffix at all - Day 47-50 deployments already have data
# in `settings.qdrant_collection_name` verbatim, and the default provider
# is (and must remain) "openai", so this preserves every existing
# collection's name exactly. Any other provider gets its own, separate
# collection - switching providers never reads or writes the other one's
# vectors, and never requires deciding how to migrate between dimensions.
_PROVIDER_COLLECTION_SUFFIXES = {"openai": ""}


class VectorStoreError(Exception):
    pass


def _base_url() -> str:
    return f"http://{settings.qdrant_host}:{settings.qdrant_port}"


def _collection_name() -> str:
    suffix = _PROVIDER_COLLECTION_SUFFIXES.get(settings.embedding_provider, f"_{settings.embedding_provider}")
    return f"{settings.qdrant_collection_name}{suffix}"


def _collection_url() -> str:
    return f"{_base_url()}/collections/{_collection_name()}"


def _existing_vector_size(collection_info: dict) -> Optional[int]:
    """Best-effort read of an existing collection's configured vector size
    from Qdrant's GET /collections/{name} response. Returns None (skip the
    check, don't crash on it) if the shape isn't what's expected - the
    per-provider collection separation above is the primary guard; this is
    defense in depth, not the only line of defense."""
    try:
        return collection_info["result"]["config"]["params"]["vectors"]["size"]
    except (KeyError, TypeError):
        return None


async def ensure_collection(vector_size: int) -> None:
    """Create the configured collection if it doesn't already exist.

    If it does exist, verify its vector size actually matches - guards
    against a manually-set `QDRANT_COLLECTION_NAME` colliding across two
    differently-configured deployments, on top of the automatic per-provider
    naming above. Raises a clear, actionable error instead of letting a
    dimension-mismatched upsert fail with a raw Qdrant error later.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(_collection_url())
            if response.status_code == 200:
                existing_size = _existing_vector_size(response.json())
                if existing_size is not None and existing_size != vector_size:
                    raise VectorStoreError(
                        f"Collection {_collection_name()!r} already stores "
                        f"{existing_size}-dimensional vectors, but the active "
                        f"embedding provider ({settings.embedding_provider!r}) "
                        f"produced a {vector_size}-dimensional one. Embedding "
                        "providers must not share a collection - switching "
                        "providers requires reindexing into a fresh "
                        "collection, never mixing dimensions in one."
                    )
                return
            if response.status_code != 404:
                raise VectorStoreError(
                    f"Qdrant returned {response.status_code} checking collection: {response.text}"
                )

            response = await client.put(
                _collection_url(),
                json={"vectors": {"size": vector_size, "distance": DISTANCE_METRIC}},
            )
    except httpx.RequestError as exc:
        raise VectorStoreError(f"Could not reach Qdrant: {exc}") from exc
    if response.status_code not in (200, 201):
        raise VectorStoreError(
            f"Qdrant returned {response.status_code} creating collection: {response.text}"
        )


async def upsert_chunks(points: list[dict]) -> None:
    """Upsert a batch of `{"id", "vector", "payload"}` points.

    Ensures the collection exists first, sized from the first point's
    vector (all vectors in a batch come from the same embedding model, so
    they're always the same length).
    """
    if not points:
        return

    await ensure_collection(len(points[0]["vector"]))

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.put(
                f"{_collection_url()}/points",
                params={"wait": "true"},
                json={"points": points},
            )
    except httpx.RequestError as exc:
        raise VectorStoreError(f"Could not reach Qdrant: {exc}") from exc
    if response.status_code != 200:
        raise VectorStoreError(
            f"Qdrant returned {response.status_code} upserting points: {response.text}"
        )


async def search(vector: list[float], repository_id: uuid.UUID, limit: int = 10) -> list[dict]:
    """Semantic search within one repository's vectors.

    Returns Qdrant's raw hits (`[{"id", "score", "payload"}, ...]`, highest
    score first) - the caller joins `payload["code_chunk_id"]` back against
    Postgres for the actual chunk content, since Qdrant only stores enough
    payload to filter and locate a hit, not the content itself.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{_collection_url()}/points/search",
                json={
                    "vector": vector,
                    "filter": {
                        "must": [{"key": "repository_id", "match": {"value": str(repository_id)}}]
                    },
                    "limit": limit,
                    "with_payload": True,
                },
            )
    except httpx.RequestError as exc:
        raise VectorStoreError(f"Could not reach Qdrant: {exc}") from exc
    if response.status_code == 404:
        return []
    if response.status_code != 200:
        raise VectorStoreError(
            f"Qdrant returned {response.status_code} searching points: {response.text}"
        )
    return response.json()["result"]


async def delete_by_repository(repository_id: uuid.UUID) -> None:
    """Delete every point belonging to a repository, filtered by payload.

    A no-op (not an error) if the collection doesn't exist yet - there's
    nothing to delete for a repository that was never successfully embedded.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{_collection_url()}/points/delete",
                params={"wait": "true"},
                json={
                    "filter": {
                        "must": [{"key": "repository_id", "match": {"value": str(repository_id)}}]
                    }
                },
            )
    except httpx.RequestError as exc:
        raise VectorStoreError(f"Could not reach Qdrant: {exc}") from exc
    if response.status_code == 404:
        return
    if response.status_code != 200:
        raise VectorStoreError(
            f"Qdrant returned {response.status_code} deleting points: {response.text}"
        )
