"""Unit tests for app.services.agent's native tool-calling declarations
(the TOOLS schema, the system prompt's prompt-injection boundary, and the
untrusted-content wrapper).

Endpoint-level tests for POST /repositories/{id}/agent (the full
plan-act loop, with mocked LLM/embedding calls) live in
test_repositories.py alongside every other repositories-router
endpoint's tests, same convention as explain/review/debug/etc.

This file previously tested `_parse_decision()`, the free-text JSON
parser the prompted-JSON planner used. That function was removed when the
planner moved to native tool calling (see agent.py's module docstring for
why) - there is no longer any raw LLM text to parse into a decision, so
these tests were replaced rather than deleted, per the same "no unnecessary
JSON parsing/fallback logic" migration that removed the function itself.
"""

from app.services.agent import TOOLS, UNTRUSTED_CONTENT_BEGIN, UNTRUSTED_CONTENT_END, AGENT_SYSTEM_PROMPT, _wrap_untrusted

EXPECTED_TOOL_NAMES = {
    "search_code",
    "explain_file",
    "review_file",
    "debug",
    "architecture",
    "security_scan",
}


def test_tools_schema_declares_all_six_tools():
    names = {tool["function"]["name"] for tool in TOOLS}
    assert names == EXPECTED_TOOL_NAMES


def test_tools_schema_entries_are_valid_function_definitions():
    for tool in TOOLS:
        assert tool["type"] == "function"
        function = tool["function"]
        assert isinstance(function["name"], str) and function["name"]
        assert isinstance(function["description"], str) and function["description"]
        parameters = function["parameters"]
        assert parameters["type"] == "object"
        assert isinstance(parameters["properties"], dict)
        assert isinstance(parameters["required"], list)
        # Every required parameter name must actually be a declared property.
        assert set(parameters["required"]).issubset(parameters["properties"])


def test_tools_requiring_no_arguments_declare_empty_schema():
    no_arg_tools = {"architecture", "security_scan"}
    for tool in TOOLS:
        if tool["function"]["name"] in no_arg_tools:
            assert tool["function"]["parameters"]["properties"] == {}
            assert tool["function"]["parameters"]["required"] == []


def test_agent_system_prompt_establishes_untrusted_content_boundary():
    assert UNTRUSTED_CONTENT_BEGIN in AGENT_SYSTEM_PROMPT
    assert UNTRUSTED_CONTENT_END in AGENT_SYSTEM_PROMPT
    assert "never a command" in AGENT_SYSTEM_PROMPT


def test_agent_system_prompt_has_no_leftover_json_contract():
    # Regression guard for the migration itself: the old prompted-JSON
    # contract ({"action": "tool", ...}) must not still be instructing the
    # model to hand-write JSON now that native tool calling replaces it.
    assert '"action"' not in AGENT_SYSTEM_PROMPT
    assert '{"action"' not in AGENT_SYSTEM_PROMPT


def test_wrap_untrusted_marks_content_with_boundary_markers():
    wrapped = _wrap_untrusted("some repository-derived text")
    assert wrapped.startswith(UNTRUSTED_CONTENT_BEGIN)
    assert wrapped.endswith(UNTRUSTED_CONTENT_END)
    assert "some repository-derived text" in wrapped
