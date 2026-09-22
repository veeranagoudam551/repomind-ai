"""LLM provider abstraction for grounded chat answers (architecture.md
Phase 3 RAG pipeline - the "LLM" step) and for the Agent's native tool
calling.

A separate module from app/services/embeddings.py by design: neither
Anthropic nor Groq has an embeddings endpoint, so embeddings (OpenAI/local)
and chat (this module) are always two different providers no matter how
`LLM_PROVIDER` is configured.

Mirrors the app/services/embedding_providers.py pattern: an `LLMProvider`
Protocol, one class per provider, one `_get_provider()` factory keyed off
`settings.llm_provider`. `generate_response()`'s signature and the two
exception types below are the only thing every non-Agent caller
(repositories.py, conversations.py, and the Agent's own LLM-backed tools in
agent.py) depends on, so adding a provider - or adding the tool-calling
method below - here changes zero of those callers.

`generate_with_tools()` is a second, independent entry point used only by
the Agent's planner (agent.py). It normalizes both providers' very
different native tool-calling wire formats (Groq: OpenAI-compatible
`tools`/`tool_calls`/role="tool" messages; Anthropic: `tools` with
`input_schema`, `tool_use`/`tool_result` content blocks) into one shared
shape so agent.py never has to know which provider it's talking to:

  messages: a list of {"role": "user"|"assistant"|"tool", ...} - assistant
  entries may carry "tool_calls": [{"id","name","arguments" (dict)}], tool
  entries carry "tool_call_id" + "content". No "system" entry belongs in
  this list - system_prompt is a separate parameter, exactly like
  generate_response().

  tools: OpenAI-style function-calling schemas -
  [{"type": "function", "function": {"name", "description", "parameters"}}]
  - the one shape agent.py builds; each provider translates it into its
  own wire format internally.

  return value: {"tool_calls": [{"id","name","arguments"}] | None,
  "content": str | None, "finish_reason": "tool_calls" | "stop" | <other>}
"""

from __future__ import annotations

import json
from typing import Any, Optional, Protocol, runtime_checkable

import httpx

from app.core.config import settings

ANTHROPIC_API_BASE = "https://api.anthropic.com/v1"
ANTHROPIC_API_VERSION = "2023-06-01"
GROQ_API_BASE = "https://api.groq.com/openai/v1"
DEFAULT_MAX_TOKENS = 1024
_REQUEST_TIMEOUT_SECONDS = 60.0

# Anthropic's `stop_reason` vocabulary differs from Groq/OpenAI's
# `finish_reason` one - normalized onto the same three values agent.py
# actually branches on (falls back to the raw Anthropic value for anything
# else, e.g. "stop_sequence", so it's never silently dropped).
_ANTHROPIC_FINISH_REASON_MAP = {
    "tool_use": "tool_calls",
    "end_turn": "stop",
    "max_tokens": "length",
}


class LLMConfigError(Exception):
    pass


class LLMAPIError(Exception):
    pass


class LLMToolChoiceViolationError(LLMAPIError):
    """Groq-specific, narrower than a generic LLMAPIError: the model
    attempted a tool call despite tool_choice="none" being sent - an
    occasional, documented behavior of some Groq models (e.g. gpt-oss)
    rejected by Groq's own API as a 400, not a generic upstream failure.
    Subclasses LLMAPIError so every existing `except LLMAPIError` still
    catches it too; only a caller that needs to react to this specific
    condition (the Agent's forced-finish turn) needs to catch it by name.
    Raised only when Groq's response carries both the structured
    "tool_use_failed" error code and this exact message, so an unrelated
    400 (or an unrelated tool_use_failed cause) still raises the plain
    LLMAPIError - never swallowed as if it were this specific case.
    """

    pass


class LLMRateLimitError(LLMAPIError):
    """Raised specifically for a provider HTTP 429 (Groq or Anthropic),
    with an already-safe-to-show-the-user message instead of the raw
    upstream response body a generic LLMAPIError carries. Subclasses
    LLMAPIError so it needs no new handling anywhere - every existing
    `except LLMAPIError as exc: raise HTTPException(..., detail=str(exc))`
    across repositories.py/conversations.py already turns whatever
    message this exception carries into the client-facing `detail`, so
    fixing the message here is enough; no endpoint or agent.py code needs
    to change. Deliberately still maps to the existing 502
    (_LLM_UPSTREAM_ERROR) status, not 429 - this API already documents
    429 as *RepoMind's own* per-user rate limit (per_user_rate_limit,
    Day 46); reusing it for "the upstream provider is rate-limited" would
    make an existing, already-documented status code ambiguous.
    """

    pass


_RATE_LIMIT_MESSAGE = "The AI service is temporarily rate-limited. Please wait a moment and try again."


def _rate_limit_message(response: httpx.Response) -> str:
    # Respects a standard Retry-After header when the provider actually
    # sends one, without assuming any specific provider always does -
    # safe/defensive, not a guess at Groq-specific header names that
    # weren't independently confirmed present on a real 429 response.
    retry_after = response.headers.get("retry-after")
    if retry_after:
        try:
            seconds = float(retry_after)
            return f"The AI service is temporarily rate-limited. Please try again in about {seconds:.0f}s."
        except ValueError:
            pass
    return _RATE_LIMIT_MESSAGE


@runtime_checkable
class LLMProvider(Protocol):
    async def generate(self, system_prompt: str, user_message: str, max_tokens: int) -> str:
        ...

    async def generate_with_tools(
        self,
        system_prompt: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int,
        tool_choice: str = "auto",
    ) -> dict:
        ...


class AnthropicLLMProvider:
    """`generate()` is unchanged behavior from before this abstraction
    existed: same endpoint, headers, model, timeout, exception mapping, and
    response parsing."""

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

        if response.status_code == 429:
            raise LLMRateLimitError(_rate_limit_message(response))
        if response.status_code != 200:
            raise LLMAPIError(
                f"Anthropic API returned {response.status_code}: {response.text}"
            )

        data = response.json()
        blocks = [block["text"] for block in data["content"] if block.get("type") == "text"]
        return "".join(blocks)

    @staticmethod
    def _to_anthropic_tools(tools: list[dict]) -> list[dict]:
        # Anthropic's tool schema is {"name","description","input_schema"} -
        # a flat sibling of OpenAI's nested {"function": {...,"parameters"}}.
        return [
            {
                "name": t["function"]["name"],
                "description": t["function"].get("description", ""),
                "input_schema": t["function"]["parameters"],
            }
            for t in tools
        ]

    @staticmethod
    def _to_anthropic_messages(messages: list[dict]) -> list[dict]:
        anthropic_messages: list[dict] = []
        for message in messages:
            role = message["role"]
            if role == "user":
                anthropic_messages.append({"role": "user", "content": message["content"]})
            elif role == "assistant":
                blocks: list[dict] = []
                if message.get("content"):
                    blocks.append({"type": "text", "text": message["content"]})
                for call in message.get("tool_calls") or []:
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": call["id"],
                            "name": call["name"],
                            "input": call["arguments"],
                        }
                    )
                anthropic_messages.append({"role": "assistant", "content": blocks})
            elif role == "tool":
                # Anthropic requires every tool_result answering one
                # assistant turn to live in a single following user turn
                # (not one user turn per result) - coalesce consecutive
                # normalized "tool" messages into the same Anthropic turn.
                result_block = {
                    "type": "tool_result",
                    "tool_use_id": message["tool_call_id"],
                    "content": message["content"],
                }
                if (
                    anthropic_messages
                    and anthropic_messages[-1]["role"] == "user"
                    and isinstance(anthropic_messages[-1]["content"], list)
                ):
                    anthropic_messages[-1]["content"].append(result_block)
                else:
                    anthropic_messages.append({"role": "user", "content": [result_block]})
        return anthropic_messages

    async def generate_with_tools(
        self,
        system_prompt: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int,
        tool_choice: str = "auto",
    ) -> dict:
        if not settings.anthropic_api_key:
            raise LLMConfigError("ANTHROPIC_API_KEY is not configured")

        headers = {
            "x-api-key": settings.anthropic_api_key,
            "anthropic-version": ANTHROPIC_API_VERSION,
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": settings.anthropic_model,
            "max_tokens": max_tokens,
            "system": system_prompt,
            "messages": self._to_anthropic_messages(messages),
        }
        # Anthropic has no "none" tool_choice (unlike Groq/OpenAI) - the
        # equivalent of "force a text-only reply" is omitting `tools`
        # entirely, since the model can't call a tool it was never given.
        if tool_choice != "none":
            payload["tools"] = self._to_anthropic_tools(tools)
            payload["tool_choice"] = {"type": "any"} if tool_choice == "required" else {"type": "auto"}

        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    f"{ANTHROPIC_API_BASE}/messages", headers=headers, json=payload
                )
        except httpx.RequestError as exc:
            raise LLMAPIError(f"Could not reach Anthropic: {exc}") from exc

        if response.status_code == 429:
            raise LLMRateLimitError(_rate_limit_message(response))
        if response.status_code != 200:
            raise LLMAPIError(
                f"Anthropic API returned {response.status_code}: {response.text}"
            )

        data = response.json()
        stop_reason = data.get("stop_reason") or "end_turn"
        finish_reason = _ANTHROPIC_FINISH_REASON_MAP.get(stop_reason, stop_reason)

        tool_calls: Optional[list[dict]] = None
        text_parts: list[str] = []
        for block in data.get("content", []):
            block_type = block.get("type")
            if block_type == "text":
                text_parts.append(block.get("text", ""))
            elif block_type == "tool_use":
                if tool_calls is None:
                    tool_calls = []
                tool_calls.append(
                    {
                        "id": block.get("id", ""),
                        "name": block.get("name", ""),
                        "arguments": block.get("input") or {},
                    }
                )

        content = "".join(text_parts) if text_parts else None
        return {"tool_calls": tool_calls, "content": content, "finish_reason": finish_reason}


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

        if response.status_code == 429:
            raise LLMRateLimitError(_rate_limit_message(response))
        if response.status_code != 200:
            raise LLMAPIError(f"Groq API returned {response.status_code}: {response.text}")

        data = response.json()
        return data["choices"][0]["message"]["content"]

    @staticmethod
    def _to_groq_messages(system_prompt: str, messages: list[dict]) -> list[dict]:
        groq_messages: list[dict] = [{"role": "system", "content": system_prompt}]
        for message in messages:
            role = message["role"]
            if role == "assistant":
                assistant_message: dict[str, Any] = {"role": "assistant", "content": message.get("content")}
                calls = message.get("tool_calls")
                if calls:
                    assistant_message["tool_calls"] = [
                        {
                            "id": call["id"],
                            "type": "function",
                            "function": {
                                "name": call["name"],
                                "arguments": json.dumps(call["arguments"]),
                            },
                        }
                        for call in calls
                    ]
                groq_messages.append(assistant_message)
            elif role == "tool":
                groq_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": message["tool_call_id"],
                        "content": message["content"],
                    }
                )
            else:
                groq_messages.append({"role": role, "content": message["content"]})
        return groq_messages

    async def generate_with_tools(
        self,
        system_prompt: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int,
        tool_choice: str = "auto",
    ) -> dict:
        if not settings.groq_api_key:
            raise LLMConfigError("GROQ_API_KEY is not configured")

        headers = {
            "Authorization": f"Bearer {settings.groq_api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": settings.groq_model,
            "max_tokens": max_tokens,
            "messages": self._to_groq_messages(system_prompt, messages),
        }
        # An empty `tools` list means "this call must not be able to call
        # any tool at all" (the Agent's tool-choice-violation fallback,
        # below) - omitting both keys rather than sending an empty `tools`
        # array is the same "no tools offered" shape Groq's own API
        # expects, and matches how AnthropicLLMProvider already handles
        # tool_choice="none" (by omitting `tools` entirely). Every existing
        # caller always passes the real TOOLS list, so this is additive,
        # not a behavior change for them.
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice

        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    f"{GROQ_API_BASE}/chat/completions", headers=headers, json=payload
                )
        except httpx.RequestError as exc:
            raise LLMAPIError(f"Could not reach Groq: {exc}") from exc

        if response.status_code == 429:
            raise LLMRateLimitError(_rate_limit_message(response))
        if response.status_code != 200:
            if response.status_code == 400:
                try:
                    error_body = response.json().get("error", {})
                except ValueError:
                    error_body = {}
                if error_body.get("code") == "tool_use_failed" and (
                    "tool choice is none" in error_body.get("message", "").lower()
                ):
                    raise LLMToolChoiceViolationError(
                        f"Groq API returned 400: {response.text}"
                    )
            raise LLMAPIError(f"Groq API returned {response.status_code}: {response.text}")

        data = response.json()
        choice = data["choices"][0]
        message = choice["message"]
        finish_reason = choice.get("finish_reason") or "stop"

        tool_calls: Optional[list[dict]] = None
        raw_tool_calls = message.get("tool_calls")
        if raw_tool_calls:
            tool_calls = []
            for call in raw_tool_calls:
                try:
                    arguments = json.loads(call["function"]["arguments"] or "{}")
                    if not isinstance(arguments, dict):
                        arguments = {}
                except (json.JSONDecodeError, TypeError, KeyError):
                    # Malformed/missing arguments from the model - never
                    # crash the request over it; the tool functions
                    # downstream already treat a missing expected key as
                    # "no value provided" and return a clean error string.
                    arguments = {}
                tool_calls.append(
                    {
                        "id": call.get("id", ""),
                        "name": call.get("function", {}).get("name", ""),
                        "arguments": arguments,
                    }
                )

        return {
            "tool_calls": tool_calls,
            "content": message.get("content") or None,
            "finish_reason": finish_reason,
        }


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


async def generate_with_tools(
    system_prompt: str,
    messages: list[dict],
    tools: list[dict],
    max_tokens: int = DEFAULT_MAX_TOKENS,
    tool_choice: str = "auto",
) -> dict:
    """Used only by the Agent's planner (agent.py) - every other caller
    keeps using generate_response() above, completely unaffected by this
    function's existence. See the module docstring for the normalized
    `messages`/`tools`/return shape both providers translate to and from.
    """
    provider = _get_provider()
    return await provider.generate_with_tools(system_prompt, messages, tools, max_tokens, tool_choice)
