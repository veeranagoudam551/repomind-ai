"""Unit tests for app.services.agent._parse_decision.

Endpoint-level tests for POST /repositories/{id}/agent (the full
plan-act loop, with mocked LLM/embedding calls) live in
test_repositories.py alongside every other repositories-router
endpoint's tests, same convention as explain/review/debug/etc.
"""

from app.services.agent import _parse_decision


def test_parse_decision_accepts_plain_json():
    decision = _parse_decision('{"action": "finish", "answer": "done"}')
    assert decision == {"action": "finish", "answer": "done"}


def test_parse_decision_strips_markdown_code_fence():
    raw = '```json\n{"action": "tool", "tool": "search_code", "arguments": {"query": "x"}}\n```'
    decision = _parse_decision(raw)
    assert decision["action"] == "tool"
    assert decision["tool"] == "search_code"


def test_parse_decision_extracts_json_from_surrounding_text():
    raw = 'Sure, here is my decision: {"action": "finish", "answer": "ok"} - hope that helps!'
    decision = _parse_decision(raw)
    assert decision == {"action": "finish", "answer": "ok"}


def test_parse_decision_falls_back_to_finish_on_unparseable_text():
    decision = _parse_decision("I think the answer is 42, no JSON here.")
    assert decision["action"] == "finish"
    assert decision["answer"] == "I think the answer is 42, no JSON here."


def test_parse_decision_falls_back_to_finish_on_unknown_action():
    decision = _parse_decision('{"action": "sleep"}')
    assert decision["action"] == "finish"
