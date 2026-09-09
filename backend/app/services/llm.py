"""LLM provider abstraction for grounded chat answers (architecture.md
Phase 3 RAG pipeline - the "LLM" step).

A separate module from app/services/embeddings.py by design: Anthropic has
no embeddings endpoint, so embeddings (OpenAI) and chat (Anthropic, via
`generate_response` below) are always two different providers no matter how
`LLM_PROVIDER` is configured. Only Anthropic is implemented today - a
second provider would add a branch on `settings.llm_provider` here without
changing this function's signature or any caller.
"""

from __future__ import annotations

import httpx

from app.core.config import settings

ANTHROPIC_API_BASE = "https://api.anthropic.com/v1"
ANTHROPIC_API_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 1024


class LLMConfigError(Exception):
    pass


class LLMAPIError(Exception):
    pass


def _require_api_key() -> str:
    if not settings.anthropic_api_key:
        raise LLMConfigError("ANTHROPIC_API_KEY is not configured")
    return settings.anthropic_api_key


async def generate_response(
    system_prompt: str, user_message: str, max_tokens: int = DEFAULT_MAX_TOKENS
) -> str:
    """Send one user turn (plus a system prompt) to Anthropic's Messages API
    and return the assistant's text reply.

    Stateless by design - the RAG endpoint is responsible for building
    `user_message` (question + retrieved context) and for persisting
    conversation history; this function has no notion of prior turns.
    """
    api_key = _require_api_key()

    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_API_VERSION,
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.anthropic_model,
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_message}],
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{ANTHROPIC_API_BASE}/messages", headers=headers, json=payload
        )

    if response.status_code != 200:
        raise LLMAPIError(
            f"Anthropic API returned {response.status_code}: {response.text}"
        )

    data = response.json()
    blocks = [block["text"] for block in data["content"] if block.get("type") == "text"]
    return "".join(blocks)
