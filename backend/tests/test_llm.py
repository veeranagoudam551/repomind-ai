"""Unit tests for app.services.llm, mocking the Anthropic/Groq HTTP calls
via httpx.MockTransport since no live ANTHROPIC_API_KEY/GROQ_API_KEY is
configured for this environment. See docs/architecture.md Day 17 for
context, and app/services/llm.py's docstring for the provider abstraction.
"""

import json

import httpx
import pytest

from app.services.llm import (
    LLMAPIError,
    LLMConfigError,
    LLMRateLimitError,
    LLMToolChoiceViolationError,
    generate_response,
    generate_with_tools,
)


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


# --- generate_with_tools() - Groq (native OpenAI-compatible tool calling) --

_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "search_code",
        "description": "Search the repository.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
}


def _groq_tool_call_response(tool_calls: list[dict], content=None) -> dict:
    return {
        "id": "chatcmpl-test",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content, "tool_calls": tool_calls},
                "finish_reason": "tool_calls",
            }
        ],
    }


async def test_generate_with_tools_groq_requires_api_key(monkeypatch):
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "groq")
    monkeypatch.setattr("app.services.llm.settings.groq_api_key", "")
    with pytest.raises(LLMConfigError):
        await generate_with_tools("system", [], [_SEARCH_TOOL], max_tokens=1024)


async def test_generate_with_tools_groq_success(monkeypatch):
    def handler(request):
        assert str(request.url) == "https://api.groq.com/openai/v1/chat/completions"
        payload = json.loads(request.content)
        assert payload["tools"] == [_SEARCH_TOOL]
        assert payload["tool_choice"] == "auto"
        return httpx.Response(
            200,
            json=_groq_tool_call_response(
                [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "search_code", "arguments": '{"query": "auth"}'},
                    }
                ]
            ),
        )

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "groq")
    monkeypatch.setattr("app.services.llm.settings.groq_api_key", "gsk-test")

    result = await generate_with_tools("system", [{"role": "user", "content": "hi"}], [_SEARCH_TOOL], 1024)
    assert result["finish_reason"] == "tool_calls"
    assert result["content"] is None
    assert result["tool_calls"] == [{"id": "call_1", "name": "search_code", "arguments": {"query": "auth"}}]


async def test_generate_with_tools_groq_handles_multiple_tool_calls(monkeypatch):
    def handler(request):
        return httpx.Response(
            200,
            json=_groq_tool_call_response(
                [
                    {"id": "call_1", "type": "function", "function": {"name": "architecture", "arguments": "{}"}},
                    {"id": "call_2", "type": "function", "function": {"name": "security_scan", "arguments": "{}"}},
                ]
            ),
        )

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "groq")
    monkeypatch.setattr("app.services.llm.settings.groq_api_key", "gsk-test")

    result = await generate_with_tools("system", [], [_SEARCH_TOOL], 1024)
    assert result["tool_calls"] == [
        {"id": "call_1", "name": "architecture", "arguments": {}},
        {"id": "call_2", "name": "security_scan", "arguments": {}},
    ]


async def test_generate_with_tools_groq_malformed_arguments_default_to_empty_dict(monkeypatch):
    # Malformed JSON in function.arguments must never crash the request -
    # falls back to {}, which every _tool_* function already treats as
    # "no value provided" (a clean "error: ..." observation), not a crash.
    def handler(request):
        return httpx.Response(
            200,
            json=_groq_tool_call_response(
                [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "search_code", "arguments": "{not valid json"},
                    }
                ]
            ),
        )

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "groq")
    monkeypatch.setattr("app.services.llm.settings.groq_api_key", "gsk-test")

    result = await generate_with_tools("system", [], [_SEARCH_TOOL], 1024)
    assert result["tool_calls"] == [{"id": "call_1", "name": "search_code", "arguments": {}}]


async def test_generate_with_tools_groq_finish_with_text_content(monkeypatch):
    def handler(request):
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "Final answer.", "tool_calls": None},
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "groq")
    monkeypatch.setattr("app.services.llm.settings.groq_api_key", "gsk-test")

    result = await generate_with_tools("system", [], [_SEARCH_TOOL], 1024)
    assert result == {"tool_calls": None, "content": "Final answer.", "finish_reason": "stop"}


async def test_generate_with_tools_groq_raises_on_api_error(monkeypatch):
    def handler(request):
        return httpx.Response(400, text='{"error": {"message": "bad request"}}')

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "groq")
    monkeypatch.setattr("app.services.llm.settings.groq_api_key", "gsk-test")

    with pytest.raises(LLMAPIError):
        await generate_with_tools("system", [], [_SEARCH_TOOL], 1024)


async def test_generate_with_tools_groq_raises_on_connection_failure(monkeypatch):
    def handler(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "groq")
    monkeypatch.setattr("app.services.llm.settings.groq_api_key", "gsk-test")

    with pytest.raises(LLMAPIError):
        await generate_with_tools("system", [], [_SEARCH_TOOL], 1024)


async def test_generate_with_tools_groq_sends_tool_choice_none(monkeypatch):
    def handler(request):
        payload = json.loads(request.content)
        assert payload["tool_choice"] == "none"
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "Done."}, "finish_reason": "stop"}]})

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "groq")
    monkeypatch.setattr("app.services.llm.settings.groq_api_key", "gsk-test")

    await generate_with_tools("system", [], [_SEARCH_TOOL], 1024, tool_choice="none")


async def test_generate_with_tools_groq_translates_assistant_and_tool_messages(monkeypatch):
    # A prior assistant tool_calls turn plus its role="tool" result must
    # round-trip into Groq's own wire shape correctly.
    def handler(request):
        payload = json.loads(request.content)
        sent = payload["messages"]
        assert sent[0] == {"role": "system", "content": "system"}
        assert sent[1] == {"role": "user", "content": "goal"}
        assert sent[2]["role"] == "assistant"
        assert sent[2]["tool_calls"] == [
            {"id": "call_1", "type": "function", "function": {"name": "search_code", "arguments": '{"query": "auth"}'}}
        ]
        assert sent[3] == {"role": "tool", "tool_call_id": "call_1", "content": "some result"}
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "Done."}, "finish_reason": "stop"}]})

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "groq")
    monkeypatch.setattr("app.services.llm.settings.groq_api_key", "gsk-test")

    messages = [
        {"role": "user", "content": "goal"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "call_1", "name": "search_code", "arguments": {"query": "auth"}}],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "some result"},
    ]
    await generate_with_tools("system", messages, [_SEARCH_TOOL], 1024)


# --- generate_with_tools() - Anthropic (native tool-use API) --------------


def _anthropic_tool_use_response(blocks: list[dict], stop_reason="tool_use") -> dict:
    return {"id": "msg_test", "type": "message", "role": "assistant", "content": blocks, "stop_reason": stop_reason}


async def test_generate_with_tools_anthropic_requires_api_key(monkeypatch):
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "anthropic")
    monkeypatch.setattr("app.services.llm.settings.anthropic_api_key", "")
    with pytest.raises(LLMConfigError):
        await generate_with_tools("system", [], [_SEARCH_TOOL], max_tokens=1024)


async def test_generate_with_tools_anthropic_success(monkeypatch):
    def handler(request):
        payload = json.loads(request.content)
        # Anthropic's tool schema is {"name","description","input_schema"},
        # not OpenAI's nested {"function": {...}}.
        assert payload["tools"] == [
            {
                "name": "search_code",
                "description": "Search the repository.",
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            }
        ]
        assert payload["tool_choice"] == {"type": "auto"}
        return httpx.Response(
            200,
            json=_anthropic_tool_use_response(
                [{"type": "tool_use", "id": "toolu_1", "name": "search_code", "input": {"query": "auth"}}]
            ),
        )

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "anthropic")
    monkeypatch.setattr("app.services.llm.settings.anthropic_api_key", "sk-ant-test")

    result = await generate_with_tools("system", [{"role": "user", "content": "hi"}], [_SEARCH_TOOL], 1024)
    assert result["finish_reason"] == "tool_calls"
    assert result["content"] is None
    assert result["tool_calls"] == [{"id": "toolu_1", "name": "search_code", "arguments": {"query": "auth"}}]


async def test_generate_with_tools_anthropic_finish_with_text_content(monkeypatch):
    def handler(request):
        return httpx.Response(
            200, json=_anthropic_tool_use_response([{"type": "text", "text": "Final answer."}], stop_reason="end_turn")
        )

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "anthropic")
    monkeypatch.setattr("app.services.llm.settings.anthropic_api_key", "sk-ant-test")

    result = await generate_with_tools("system", [], [_SEARCH_TOOL], 1024)
    assert result == {"tool_calls": None, "content": "Final answer.", "finish_reason": "stop"}


async def test_generate_with_tools_anthropic_maps_max_tokens_finish_reason(monkeypatch):
    def handler(request):
        return httpx.Response(200, json=_anthropic_tool_use_response([], stop_reason="max_tokens"))

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "anthropic")
    monkeypatch.setattr("app.services.llm.settings.anthropic_api_key", "sk-ant-test")

    result = await generate_with_tools("system", [], [_SEARCH_TOOL], 1024)
    assert result["finish_reason"] == "length"
    assert result["tool_calls"] is None
    assert result["content"] is None


async def test_generate_with_tools_anthropic_tool_choice_none_omits_tools(monkeypatch):
    # Anthropic has no "none" tool_choice - the equivalent of forcing a
    # text-only reply is omitting `tools` entirely.
    def handler(request):
        payload = json.loads(request.content)
        assert "tools" not in payload
        assert "tool_choice" not in payload
        return httpx.Response(200, json=_anthropic_tool_use_response([{"type": "text", "text": "Done."}], stop_reason="end_turn"))

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "anthropic")
    monkeypatch.setattr("app.services.llm.settings.anthropic_api_key", "sk-ant-test")

    await generate_with_tools("system", [], [_SEARCH_TOOL], 1024, tool_choice="none")


async def test_generate_with_tools_anthropic_raises_on_api_error(monkeypatch):
    def handler(request):
        return httpx.Response(401, text='{"error": "invalid api key"}')

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "anthropic")
    monkeypatch.setattr("app.services.llm.settings.anthropic_api_key", "sk-ant-bad")

    with pytest.raises(LLMAPIError):
        await generate_with_tools("system", [], [_SEARCH_TOOL], 1024)


async def test_generate_with_tools_anthropic_raises_on_connection_failure(monkeypatch):
    def handler(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "anthropic")
    monkeypatch.setattr("app.services.llm.settings.anthropic_api_key", "sk-ant-test")

    with pytest.raises(LLMAPIError):
        await generate_with_tools("system", [], [_SEARCH_TOOL], 1024)


async def test_generate_with_tools_anthropic_translates_assistant_and_tool_messages(monkeypatch):
    # A prior assistant tool_calls turn becomes a content-block assistant
    # message; the matching role="tool" result becomes a tool_result block
    # inside the *next* user turn (Anthropic's own required shape) rather
    # than a separate "tool" role, which Anthropic's API doesn't have.
    def handler(request):
        payload = json.loads(request.content)
        sent = payload["messages"]
        assert sent[0] == {"role": "user", "content": "goal"}
        assert sent[1] == {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "call_1", "name": "search_code", "input": {"query": "auth"}}],
        }
        assert sent[2] == {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "some result"}],
        }
        return httpx.Response(200, json=_anthropic_tool_use_response([{"type": "text", "text": "Done."}], stop_reason="end_turn"))

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "anthropic")
    monkeypatch.setattr("app.services.llm.settings.anthropic_api_key", "sk-ant-test")

    messages = [
        {"role": "user", "content": "goal"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "call_1", "name": "search_code", "arguments": {"query": "auth"}}],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "some result"},
    ]
    await generate_with_tools("system", messages, [_SEARCH_TOOL], 1024)


async def test_generate_with_tools_anthropic_coalesces_multiple_tool_results(monkeypatch):
    # Two consecutive role="tool" normalized messages (answering two
    # parallel tool calls from the same assistant turn) must become ONE
    # Anthropic user turn with two tool_result blocks, not two separate
    # user turns - Anthropic requires all tool_results for one assistant
    # turn to live together.
    def handler(request):
        payload = json.loads(request.content)
        sent = payload["messages"]
        assert sent[-1]["role"] == "user"
        assert sent[-1]["content"] == [
            {"type": "tool_result", "tool_use_id": "call_1", "content": "result one"},
            {"type": "tool_result", "tool_use_id": "call_2", "content": "result two"},
        ]
        return httpx.Response(200, json=_anthropic_tool_use_response([{"type": "text", "text": "Done."}], stop_reason="end_turn"))

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "anthropic")
    monkeypatch.setattr("app.services.llm.settings.anthropic_api_key", "sk-ant-test")

    messages = [
        {"role": "user", "content": "goal"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "call_1", "name": "architecture", "arguments": {}},
                {"id": "call_2", "name": "security_scan", "arguments": {}},
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "result one"},
        {"role": "tool", "tool_call_id": "call_2", "content": "result two"},
    ]
    await generate_with_tools("system", messages, [_SEARCH_TOOL], 1024)


# --- generate_with_tools() - tool_choice="none" violation (Groq-specific) -


def _groq_tool_choice_violation_response() -> dict:
    return {
        "error": {
            "message": "Tool choice is none, but model called a tool",
            "type": "invalid_request_error",
            "code": "tool_use_failed",
            "failed_generation": '{"name": "search_code", "arguments": {"query": "auth"}}',
        }
    }


async def test_generate_with_tools_groq_raises_tool_choice_violation_precisely(monkeypatch):
    def handler(request):
        return httpx.Response(400, json=_groq_tool_choice_violation_response())

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "groq")
    monkeypatch.setattr("app.services.llm.settings.groq_api_key", "gsk-test")

    with pytest.raises(LLMToolChoiceViolationError):
        await generate_with_tools("system", [], [_SEARCH_TOOL], 1024, tool_choice="none")


async def test_generate_with_tools_groq_tool_choice_violation_is_still_an_llm_api_error(monkeypatch):
    # Subclassing means every existing `except LLMAPIError` handler still
    # catches this specific case too, without needing to know about it.
    def handler(request):
        return httpx.Response(400, json=_groq_tool_choice_violation_response())

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "groq")
    monkeypatch.setattr("app.services.llm.settings.groq_api_key", "gsk-test")

    with pytest.raises(LLMAPIError):
        await generate_with_tools("system", [], [_SEARCH_TOOL], 1024, tool_choice="none")


async def test_generate_with_tools_groq_arbitrary_400_is_not_misclassified(monkeypatch):
    # A 400 with a different cause (even the same "tool_use_failed" code,
    # for a different reason, or an unrelated 400 entirely) must never be
    # swallowed as if it were the specific tool-choice violation - only
    # the exact documented condition gets the narrower exception type.
    def handler(request):
        return httpx.Response(
            400,
            json={
                "error": {
                    "message": "The model produced invalid arguments",
                    "type": "invalid_request_error",
                    "code": "tool_use_failed",
                }
            },
        )

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "groq")
    monkeypatch.setattr("app.services.llm.settings.groq_api_key", "gsk-test")

    with pytest.raises(LLMAPIError) as exc_info:
        await generate_with_tools("system", [], [_SEARCH_TOOL], 1024)
    assert not isinstance(exc_info.value, LLMToolChoiceViolationError)


async def test_generate_with_tools_groq_unrelated_400_is_plain_api_error(monkeypatch):
    def handler(request):
        return httpx.Response(400, text='{"error": {"message": "bad request", "code": "other"}}')

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "groq")
    monkeypatch.setattr("app.services.llm.settings.groq_api_key", "gsk-test")

    with pytest.raises(LLMAPIError) as exc_info:
        await generate_with_tools("system", [], [_SEARCH_TOOL], 1024)
    assert not isinstance(exc_info.value, LLMToolChoiceViolationError)


async def test_generate_with_tools_groq_empty_tools_list_omits_tools_and_tool_choice(monkeypatch):
    # The Agent's tool-choice-violation fallback passes tools=[] to make a
    # request that structurally cannot call any tool - must omit both
    # keys entirely, not send an empty `tools` array (which some APIs
    # reject when paired with a tool_choice).
    def handler(request):
        payload = json.loads(request.content)
        assert "tools" not in payload
        assert "tool_choice" not in payload
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "Done."}, "finish_reason": "stop"}]})

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "groq")
    monkeypatch.setattr("app.services.llm.settings.groq_api_key", "gsk-test")

    result = await generate_with_tools("system", [], [], 1024, tool_choice="none")
    assert result["content"] == "Done."


# --- Provider HTTP 429 -> LLMRateLimitError (clean, user-safe message) ----


async def test_generate_response_groq_429_raises_rate_limit_error_with_clean_message(monkeypatch):
    def handler(request):
        return httpx.Response(
            429,
            text='{"error":{"message":"Rate limit reached for model `openai/gpt-oss-120b`... '
            '(Requested 17985, Used 7283)","type":"tokens","code":"rate_limit_exceeded"}}',
        )

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "groq")
    monkeypatch.setattr("app.services.llm.settings.groq_api_key", "gsk-test")

    with pytest.raises(LLMRateLimitError) as exc_info:
        await generate_response("system", "hello")
    message = str(exc_info.value)
    # The raw upstream JSON/account numbers must never reach the message
    # that ends up as the client-facing HTTPException detail.
    assert "rate_limit_exceeded" not in message
    assert "17985" not in message
    assert "Requested" not in message
    assert "temporarily rate-limited" in message.lower()


async def test_generate_response_groq_429_is_still_an_llm_api_error(monkeypatch):
    # Subclassing LLMAPIError means every existing `except LLMAPIError`
    # in repositories.py/conversations.py already handles this correctly
    # with zero endpoint-level code changes.
    def handler(request):
        return httpx.Response(429, text='{"error": {"message": "rate limited"}}')

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "groq")
    monkeypatch.setattr("app.services.llm.settings.groq_api_key", "gsk-test")

    with pytest.raises(LLMAPIError):
        await generate_response("system", "hello")


async def test_generate_response_groq_429_respects_retry_after_header(monkeypatch):
    def handler(request):
        return httpx.Response(
            429, headers={"retry-after": "12"}, text='{"error": {"message": "rate limited"}}'
        )

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "groq")
    monkeypatch.setattr("app.services.llm.settings.groq_api_key", "gsk-test")

    with pytest.raises(LLMRateLimitError) as exc_info:
        await generate_response("system", "hello")
    assert "12s" in str(exc_info.value)


async def test_generate_response_groq_429_without_retry_after_uses_generic_message(monkeypatch):
    def handler(request):
        return httpx.Response(429, text='{"error": {"message": "rate limited"}}')

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "groq")
    monkeypatch.setattr("app.services.llm.settings.groq_api_key", "gsk-test")

    with pytest.raises(LLMRateLimitError) as exc_info:
        await generate_response("system", "hello")
    assert str(exc_info.value) == (
        "The AI service is temporarily rate-limited. Please wait a moment and try again."
    )


async def test_generate_with_tools_groq_429_raises_rate_limit_error(monkeypatch):
    def handler(request):
        return httpx.Response(429, text='{"error": {"message": "rate limited"}}')

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "groq")
    monkeypatch.setattr("app.services.llm.settings.groq_api_key", "gsk-test")

    with pytest.raises(LLMRateLimitError):
        await generate_with_tools("system", [], [_SEARCH_TOOL], 1024)


async def test_generate_with_tools_anthropic_429_raises_rate_limit_error(monkeypatch):
    def handler(request):
        return httpx.Response(429, text='{"error": {"message": "rate limited"}}')

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "anthropic")
    monkeypatch.setattr("app.services.llm.settings.anthropic_api_key", "sk-ant-test")

    with pytest.raises(LLMRateLimitError):
        await generate_with_tools("system", [], [_SEARCH_TOOL], 1024)


async def test_generate_response_anthropic_429_raises_rate_limit_error(monkeypatch):
    def handler(request):
        return httpx.Response(429, text='{"type": "error", "error": {"message": "rate limited"}}')

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "anthropic")
    monkeypatch.setattr("app.services.llm.settings.anthropic_api_key", "sk-ant-test")

    with pytest.raises(LLMRateLimitError):
        await generate_response("system", "hello")


async def test_generate_with_tools_groq_400_unrelated_to_tool_choice_stays_plain_api_error(monkeypatch):
    # A normal (non-tool-choice, non-rate-limit) 400 must NOT be
    # reclassified as a rate limit or a tool-choice violation.
    def handler(request):
        return httpx.Response(400, text='{"error": {"message": "invalid request", "code": "invalid_request_error"}}')

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "groq")
    monkeypatch.setattr("app.services.llm.settings.groq_api_key", "gsk-test")

    with pytest.raises(LLMAPIError) as exc_info:
        await generate_with_tools("system", [], [_SEARCH_TOOL], 1024)
    assert not isinstance(exc_info.value, LLMRateLimitError)
    assert not isinstance(exc_info.value, LLMToolChoiceViolationError)


async def test_generate_response_groq_500_stays_plain_api_error(monkeypatch):
    def handler(request):
        return httpx.Response(500, text="internal server error")

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "groq")
    monkeypatch.setattr("app.services.llm.settings.groq_api_key", "gsk-test")

    with pytest.raises(LLMAPIError) as exc_info:
        await generate_response("system", "hello")
    assert not isinstance(exc_info.value, LLMRateLimitError)


async def test_generate_response_groq_timeout_stays_plain_api_error(monkeypatch):
    def handler(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "groq")
    monkeypatch.setattr("app.services.llm.settings.groq_api_key", "gsk-test")

    with pytest.raises(LLMAPIError) as exc_info:
        await generate_response("system", "hello")
    assert not isinstance(exc_info.value, LLMRateLimitError)
