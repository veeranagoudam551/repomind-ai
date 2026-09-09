"""Qdrant vector store client (architecture.md Phase 2/3 - vector storage).

Thin REST wrapper over Qdrant's HTTP API, mirroring the style of
app/services/github.py and app/services/embeddings.py (a plain httpx call
per operation) rather than pulling in the `qdrant-client` SDK for the three
operations the ingestion pipeline needs: create-collection-if-missing,
upsert points, and delete-by-filter.

Point IDs are the `code_chunks.id` UUID itself, so once a chunk is embedded,
`code_chunks.vector_id` (added Day 8) is always just `str(chunk.id)` - no
separate ID scheme to keep in sync between Postgres and Qdrant.
"""

from __future__ import annotations

import uuid

import httpx

from app.core.config import settings

DISTANCE_METRIC = "Cosine"


class VectorStoreError(Exception):
    pass


def _base_url() -> str:
    return f"http://{settings.qdrant_host}:{settings.qdrant_port}"


def _collection_url() -> str:
    return f"{_base_url()}/collections/{settings.qdrant_collection_name}"


async def ensure_collection(vector_size: int) -> None:
    """Create the configured collection if it doesn't already exist."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(_collection_url())
        if response.status_code == 200:
            return
        if response.status_code != 404:
            raise VectorStoreError(
                f"Qdrant returned {response.status_code} checking collection: {response.text}"
            )

        response = await client.put(
            _collection_url(),
            json={"vectors": {"size": vector_size, "distance": DISTANCE_METRIC}},
        )
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

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.put(
            f"{_collection_url()}/points",
            params={"wait": "true"},
            json={"points": points},
        )
    if response.status_code != 200:
        raise VectorStoreError(
            f"Qdrant returned {response.status_code} upserting points: {response.text}"
        )


async def delete_by_repository(repository_id: uuid.UUID) -> None:
    """Delete every point belonging to a repository, filtered by payload.

    A no-op (not an error) if the collection doesn't exist yet - there's
    nothing to delete for a repository that was never successfully embedded.
    """
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
    if response.status_code == 404:
        return
    if response.status_code != 200:
        raise VectorStoreError(
            f"Qdrant returned {response.status_code} deleting points: {response.text}"
        )
