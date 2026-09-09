"""Unit tests for app.services.llm, mocking the Anthropic HTTP call via
httpx.MockTransport since no live ANTHROPIC_API_KEY is configured for this
environment. See docs/architecture.md Day 17 for context.
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


async def test_generate_response_requires_api_key(monkeypatch):
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
    monkeypatch.setattr("app.services.llm.settings.anthropic_api_key", "sk-ant-test")

    answer = await generate_response("system", "hello")
    assert answer == "part one. part two."


async def test_generate_response_raises_on_api_error(monkeypatch):
    def handler(request):
        return httpx.Response(401, text='{"error": "invalid api key"}')

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.llm.settings.anthropic_api_key", "sk-ant-bad")

    with pytest.raises(LLMAPIError):
        await generate_response("system", "hello")


async def test_generate_response_sends_configured_model_and_max_tokens(monkeypatch):
    def handler(request):
        payload = json.loads(request.content)
        assert payload["model"] == "claude-sonnet-5"
        assert payload["max_tokens"] == 256
        return httpx.Response(200, json=_anthropic_response("ok"))

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.llm.settings.anthropic_api_key", "sk-ant-test")
    monkeypatch.setattr("app.services.llm.settings.anthropic_model", "claude-sonnet-5")

    await generate_response("system", "hello", max_tokens=256)
