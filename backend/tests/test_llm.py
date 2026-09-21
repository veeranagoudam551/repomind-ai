"""Unit tests for app.services.llm, mocking the Anthropic/Groq HTTP calls
via httpx.MockTransport since no live ANTHROPIC_API_KEY/GROQ_API_KEY is
configured for this environment. See docs/architecture.md Day 17 for
context, and app/services/llm.py's docstring for the provider abstraction.
"""

import json

import httpx
import pytest

from app.services.llm import LLMAPIError, LLMConfigError, generate_response


def _install_mock_transport(monkeypatch, handler):
    transport = httpx.MockTransport(handler)

    class FakeAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", FakeAsyncClient)


def _anthropic_response(text: str) -> dict:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
        "model": "claude-sonnet-5",
    }


def _groq_response(text: str) -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
        ],
        "model": "openai/gpt-oss-120b",
    }


# --- Anthropic (existing behavior, unchanged) --------------------------


async def test_generate_response_requires_api_key(monkeypatch):
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "anthropic")
    monkeypatch.setattr("app.services.llm.settings.anthropic_api_key", "")
    with pytest.raises(LLMConfigError):
        await generate_response("system", "hello")


async def test_generate_response_success(monkeypatch):
    def handler(request):
        assert request.headers["x-api-key"] == "sk-ant-test"
        assert request.headers["anthropic-version"] == "2023-06-01"
        payload = json.loads(request.content)
        assert payload["system"] == "You are a helpful assistant."
        assert payload["messages"] == [{"role": "user", "content": "hello"}]
        return httpx.Response(200, json=_anthropic_response("hi there"))

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "anthropic")
    monkeypatch.setattr("app.services.llm.settings.anthropic_api_key", "sk-ant-test")

    answer = await generate_response("You are a helpful assistant.", "hello")
    assert answer == "hi there"


async def test_generate_response_concatenates_multiple_text_blocks(monkeypatch):
    def handler(request):
        response = _anthropic_response("")
        response["content"] = [
            {"type": "text", "text": "part one. "},
            {"type": "text", "text": "part two."},
        ]
        return httpx.Response(200, json=response)

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "anthropic")
    monkeypatch.setattr("app.services.llm.settings.anthropic_api_key", "sk-ant-test")

    answer = await generate_response("system", "hello")
    assert answer == "part one. part two."


async def test_generate_response_raises_on_api_error(monkeypatch):
    def handler(request):
        return httpx.Response(401, text='{"error": "invalid api key"}')

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "anthropic")
    monkeypatch.setattr("app.services.llm.settings.anthropic_api_key", "sk-ant-bad")

    with pytest.raises(LLMAPIError):
        await generate_response("system", "hello")


async def test_generate_response_raises_on_connection_failure(monkeypatch):
    # Day 42: a fully unreachable Anthropic raises a raw httpx.RequestError
    # with no .status_code to check - this must become an LLMAPIError, not
    # propagate raw past every caller's `except LLMAPIError`.
    def handler(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "anthropic")
    monkeypatch.setattr("app.services.llm.settings.anthropic_api_key", "sk-ant-test")

    with pytest.raises(LLMAPIError):
        await generate_response("system", "hello")


async def test_generate_response_sends_configured_model_and_max_tokens(monkeypatch):
    def handler(request):
        payload = json.loads(request.content)
        assert payload["model"] == "claude-sonnet-5"
        assert payload["max_tokens"] == 256
        return httpx.Response(200, json=_anthropic_response("ok"))

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "anthropic")
    monkeypatch.setattr("app.services.llm.settings.anthropic_api_key", "sk-ant-test")
    monkeypatch.setattr("app.services.llm.settings.anthropic_model", "claude-sonnet-5")

    await generate_response("system", "hello", max_tokens=256)


# --- Groq (new) ----------------------------------------------------------


async def test_generate_response_groq_requires_api_key(monkeypatch):
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "groq")
    monkeypatch.setattr("app.services.llm.settings.groq_api_key", "")
    with pytest.raises(LLMConfigError):
        await generate_response("system", "hello")


async def test_generate_response_groq_success(monkeypatch):
    def handler(request):
        assert str(request.url) == "https://api.groq.com/openai/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer gsk-test"
        payload = json.loads(request.content)
        assert payload["messages"] == [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "hello"},
        ]
        return httpx.Response(200, json=_groq_response("hi from groq"))

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "groq")
    monkeypatch.setattr("app.services.llm.settings.groq_api_key", "gsk-test")

    answer = await generate_response("You are a helpful assistant.", "hello")
    assert answer == "hi from groq"


async def test_generate_response_groq_raises_on_api_error(monkeypatch):
    # Covers Groq's free-tier rate limit response (429) - must map to the
    # same clean LLMAPIError every caller already handles, not crash.
    def handler(request):
        return httpx.Response(429, text='{"error": "rate limit exceeded"}')

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "groq")
    monkeypatch.setattr("app.services.llm.settings.groq_api_key", "gsk-test")

    with pytest.raises(LLMAPIError):
        await generate_response("system", "hello")


async def test_generate_response_groq_raises_on_connection_failure(monkeypatch):
    def handler(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "groq")
    monkeypatch.setattr("app.services.llm.settings.groq_api_key", "gsk-test")

    with pytest.raises(LLMAPIError):
        await generate_response("system", "hello")


async def test_generate_response_groq_sends_configured_model_and_max_tokens(monkeypatch):
    def handler(request):
        payload = json.loads(request.content)
        assert payload["model"] == "openai/gpt-oss-120b"
        assert payload["max_tokens"] == 256
        return httpx.Response(200, json=_groq_response("ok"))

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "groq")
    monkeypatch.setattr("app.services.llm.settings.groq_api_key", "gsk-test")
    monkeypatch.setattr("app.services.llm.settings.groq_model", "openai/gpt-oss-120b")

    await generate_response("system", "hello", max_tokens=256)


async def test_generate_response_groq_parses_response_content(monkeypatch):
    def handler(request):
        return httpx.Response(200, json=_groq_response("parsed content"))

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "groq")
    monkeypatch.setattr("app.services.llm.settings.groq_api_key", "gsk-test")

    answer = await generate_response("system", "hello")
    assert answer == "parsed content"


# --- Provider switch / regression -----------------------------------------


async def test_llm_provider_anthropic_selects_anthropic_endpoint(monkeypatch):
    seen_urls = []

    def handler(request):
        seen_urls.append(str(request.url))
        return httpx.Response(200, json=_anthropic_response("anthropic reply"))

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "anthropic")
    monkeypatch.setattr("app.services.llm.settings.anthropic_api_key", "sk-ant-test")

    answer = await generate_response("system", "hello")
    assert answer == "anthropic reply"
    assert seen_urls == ["https://api.anthropic.com/v1/messages"]


async def test_llm_provider_groq_selects_groq_endpoint(monkeypatch):
    seen_urls = []

    def handler(request):
        seen_urls.append(str(request.url))
        return httpx.Response(200, json=_groq_response("groq reply"))

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "groq")
    monkeypatch.setattr("app.services.llm.settings.groq_api_key", "gsk-test")

    answer = await generate_response("system", "hello")
    assert answer == "groq reply"
    assert seen_urls == ["https://api.groq.com/openai/v1/chat/completions"]


async def test_llm_provider_unknown_raises_config_error(monkeypatch):
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "not-a-real-provider")
    with pytest.raises(LLMConfigError):
        await generate_response("system", "hello")
