"""LLM provider abstraction for grounded chat answers (architecture.md
Phase 3 RAG pipeline - the "LLM" step).

A separate module from app/services/embeddings.py by design: neither
Anthropic nor Groq has an embeddings endpoint, so embeddings (OpenAI/local)
and chat (this module) are always two different providers no matter how
`LLM_PROVIDER` is configured.

Mirrors the app/services/embedding_providers.py pattern: an `LLMProvider`
Protocol, one class per provider, one `_get_provider()` factory keyed off
`settings.llm_provider`. `generate_response()`'s signature and the two
exception types below are the only thing every caller (repositories.py,
conversations.py, agent.py) depends on, so adding a provider here changes
zero callers.
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

import httpx

from app.core.config import settings

ANTHROPIC_API_BASE = "https://api.anthropic.com/v1"
ANTHROPIC_API_VERSION = "2023-06-01"
GROQ_API_BASE = "https://api.groq.com/openai/v1"
DEFAULT_MAX_TOKENS = 1024
_REQUEST_TIMEOUT_SECONDS = 60.0


class LLMConfigError(Exception):
    pass


class LLMAPIError(Exception):
    pass


@runtime_checkable
class LLMProvider(Protocol):
    async def generate(self, system_prompt: str, user_message: str, max_tokens: int) -> str:
        ...


class AnthropicLLMProvider:
    """Unchanged behavior from before this abstraction existed: same
    endpoint, headers, model, timeout, exception mapping, and response
    parsing."""

    async def generate(self, system_prompt: str, user_message: str, max_tokens: int) -> str:
        if not settings.anthropic_api_key:
            raise LLMConfigError("ANTHROPIC_API_KEY is not configured")

        headers = {
            "x-api-key": settings.anthropic_api_key,
            "anthropic-version": ANTHROPIC_API_VERSION,
            "Content-Type": "application/json",
        }
        payload = {
            "model": settings.anthropic_model,
            "max_tokens": max_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_message}],
        }

        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    f"{ANTHROPIC_API_BASE}/messages", headers=headers, json=payload
                )
        except httpx.RequestError as exc:
            raise LLMAPIError(f"Could not reach Anthropic: {exc}") from exc

        if response.status_code != 200:
            raise LLMAPIError(
                f"Anthropic API returned {response.status_code}: {response.text}"
            )

        data = response.json()
        blocks = [block["text"] for block in data["content"] if block.get("type") == "text"]
        return "".join(blocks)


class GroqLLMProvider:
    """Groq's OpenAI-compatible chat-completions endpoint - same raw-httpx
    approach as Anthropic above, no new dependency (no Groq/OpenAI SDK)."""

    async def generate(self, system_prompt: str, user_message: str, max_tokens: int) -> str:
        if not settings.groq_api_key:
            raise LLMConfigError("GROQ_API_KEY is not configured")

        headers = {
            "Authorization": f"Bearer {settings.groq_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": settings.groq_model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        }

        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    f"{GROQ_API_BASE}/chat/completions", headers=headers, json=payload
                )
        except httpx.RequestError as exc:
            raise LLMAPIError(f"Could not reach Groq: {exc}") from exc

        if response.status_code != 200:
            raise LLMAPIError(f"Groq API returned {response.status_code}: {response.text}")

        data = response.json()
        return data["choices"][0]["message"]["content"]


_PROVIDERS = {
    "anthropic": AnthropicLLMProvider,
    "groq": GroqLLMProvider,
}


def _get_provider(provider_name: Optional[str] = None) -> LLMProvider:
    """`provider_name` is only ever overridden by tests; every real caller
    uses the configured `LLM_PROVIDER`."""
    name = provider_name if provider_name is not None else settings.llm_provider
    provider_cls = _PROVIDERS.get(name)
    if provider_cls is None:
        raise LLMConfigError(
            f"Unknown LLM_PROVIDER {name!r} - expected one of {sorted(_PROVIDERS)}"
        )
    return provider_cls()


async def generate_response(
    system_prompt: str, user_message: str, max_tokens: int = DEFAULT_MAX_TOKENS
) -> str:
    """Send one user turn (plus a system prompt) to the configured LLM
    provider and return the assistant's text reply.

    Stateless by design - the RAG endpoint is responsible for building
    `user_message` (question + retrieved context) and for persisting
    conversation history; this function has no notion of prior turns.
    """
    provider = _get_provider()
    return await provider.generate(system_prompt, user_message, max_tokens)
