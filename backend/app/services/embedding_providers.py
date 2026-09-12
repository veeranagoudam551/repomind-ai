"""Embedding provider abstraction (Day 51).

`app/services/embeddings.py` (Day 14) is left completely untouched - it's
the OpenAI implementation itself, and its own tests
(`tests/test_embeddings.py`) keep exercising that HTTP call directly. This
module is the new layer every *caller* (ingestion, search, debug, chat,
the agent's tools) goes through instead: `get_embedding_provider()` reads
`EMBEDDING_PROVIDER` and returns either that same OpenAI implementation
wrapped behind the `EmbeddingProvider` protocol below, or the new local
(in-process, no API key) one - so switching providers is one env var, not
a code change, and a third provider later means one more class here, not
touching any caller.

Both implementations raise the exact same two exception types
`embeddings.py` already defines (`EmbeddingConfigError`/
`EmbeddingAPIError`) - every existing `except EmbeddingConfigError`/
`except EmbeddingAPIError` in app/api and app/services already maps these
to a clean 503/502 response or a `status: failed` ingestion row, so the
local provider gets that same safe handling for free with zero caller
changes for its own errors either.
"""

from __future__ import annotations

import asyncio
import logging
from typing import List, Optional, Protocol, runtime_checkable

from app.core.config import settings
from app.services.embeddings import (
    EmbeddingAPIError,
    EmbeddingConfigError,
    generate_embedding,
    generate_embeddings,
)

logger = logging.getLogger(__name__)


@runtime_checkable
class EmbeddingProvider(Protocol):
    async def embed_texts(self, texts: List[str]) -> List[List[float]]:
        ...

    async def embed_query(self, text: str) -> List[float]:
        ...


class OpenAIEmbeddingProvider:
    """Thin adapter over the existing, unmodified OpenAI implementation -
    behavior (batching, error types, retries-none-by-design) is exactly
    what it was before this abstraction existed."""

    async def embed_texts(self, texts: List[str]) -> List[List[float]]:
        return await generate_embeddings(texts)

    async def embed_query(self, text: str) -> List[float]:
        return await generate_embedding(text)


# Lazily loaded, module-level: `fastembed` is only ever imported - and the
# ONNX model only ever downloaded/loaded - the first time a request
# actually needs the local provider (Phase 3's requirement that selecting
# EMBEDDING_PROVIDER=openai never touches this at all). Loading takes
# several seconds to tens of seconds the first time (model download +
# ONNX session init, observed ~20s cold on this dev machine) and is
# reused for the rest of the process's life rather than repeated per call.
_local_model = None


def _load_local_model():
    global _local_model
    if _local_model is None:
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise EmbeddingConfigError(
                "EMBEDDING_PROVIDER=local requires the 'fastembed' package "
                "to be installed"
            ) from exc
        try:
            _local_model = TextEmbedding(model_name=settings.local_embedding_model)
        except Exception:
            # Never the raw exception text here - it can legitimately
            # contain a local cache file path (huggingface_hub/onnxruntime
            # error messages do), which has no business in a client-facing
            # 503/502 detail. The real cause is still fully in the server
            # log via logger.exception.
            logger.exception(
                "Failed to load local embedding model %r", settings.local_embedding_model
            )
            raise EmbeddingAPIError(
                "Local embedding model could not be loaded - check server logs"
            ) from None
    return _local_model


def _embed_sync(texts: List[str]) -> List[List[float]]:
    """Runs on a worker thread (see `asyncio.to_thread` below) - fastembed's
    `.embed()` is a synchronous, CPU-bound generator, and calling it
    directly on the event loop would block every other concurrent request
    this process is serving for however long inference takes."""
    model = _load_local_model()
    try:
        return [vector.tolist() for vector in model.embed(texts)]
    except Exception:
        logger.exception("Local embedding generation failed for %d text(s)", len(texts))
        raise EmbeddingAPIError("Local embedding generation failed - check server logs") from None


class LocalEmbeddingProvider:
    """Runs entirely in-process via fastembed (ONNX runtime, CPU) - no API
    key, no outbound network call per request. The one exception is the
    model's first download, cached by huggingface_hub under the user/
    container's home directory - never this git repo, never the Docker
    image itself."""

    async def embed_texts(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        return await asyncio.to_thread(_embed_sync, texts)

    async def embed_query(self, text: str) -> List[float]:
        (vector,) = await self.embed_texts([text])
        return vector


_PROVIDERS = {
    "openai": OpenAIEmbeddingProvider,
    "local": LocalEmbeddingProvider,
}


def get_embedding_provider(provider_name: Optional[str] = None) -> EmbeddingProvider:
    """`provider_name` is only ever overridden by tests; every real caller
    uses the configured `EMBEDDING_PROVIDER`."""
    name = provider_name if provider_name is not None else settings.embedding_provider
    provider_cls = _PROVIDERS.get(name)
    if provider_cls is None:
        raise EmbeddingConfigError(
            f"Unknown EMBEDDING_PROVIDER {name!r} - expected one of {sorted(_PROVIDERS)}"
        )
    return provider_cls()
