"""Embedding generation (architecture.md Phase 2 - Embedding Generation).

Turns a `code_chunks` row's text into a vector via OpenAI's embeddings API,
ahead of storage in Qdrant (Day 15). Kept as its own module, independent of
the ingestion pipeline and of whatever LLM provider eventually answers
questions in the RAG pipeline (Phase 3) — Anthropic doesn't offer an
embeddings endpoint, so this is deliberately OpenAI-specific rather than
part of a shared "LLM provider" abstraction.
"""

from __future__ import annotations

import httpx

from app.core.config import settings

OPENAI_API_BASE = "https://api.openai.com/v1"

# OpenAI accepts up to 2048 inputs per embeddings request; batching well
# under that keeps a single request's total token count reasonable too.
MAX_BATCH_SIZE = 100


class EmbeddingConfigError(Exception):
    pass


class EmbeddingAPIError(Exception):
    pass


def _require_api_key() -> str:
    if not settings.openai_api_key:
        raise EmbeddingConfigError("OPENAI_API_KEY is not configured")
    return settings.openai_api_key


async def _embed_batch(texts: list[str], api_key: str) -> list[list[float]]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {"model": settings.embedding_model, "input": texts}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{OPENAI_API_BASE}/embeddings", headers=headers, json=payload
            )
    except httpx.RequestError as exc:
        raise EmbeddingAPIError(f"Could not reach OpenAI: {exc}") from exc

    if response.status_code != 200:
        raise EmbeddingAPIError(
            f"OpenAI embeddings API returned {response.status_code}: {response.text}"
        )

    data = response.json()
    # The API documents `data` as already sorted by `index`, but sort
    # defensively so a reordered response can't silently misalign vectors
    # with the texts that produced them.
    items = sorted(data["data"], key=lambda item: item["index"])
    return [item["embedding"] for item in items]


async def generate_embeddings(texts: list[str]) -> list[list[float]]:
    """Embed each string in `texts`, preserving order. Empty list in, empty list out."""
    if not texts:
        return []

    api_key = _require_api_key()

    results: list[list[float]] = []
    for start in range(0, len(texts), MAX_BATCH_SIZE):
        batch = texts[start : start + MAX_BATCH_SIZE]
        results.extend(await _embed_batch(batch, api_key))
    return results


async def generate_embedding(text: str) -> list[float]:
    (embedding,) = await generate_embeddings([text])
    return embedding
