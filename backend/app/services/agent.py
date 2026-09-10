"""Specialized AI agent for complex, multi-step tasks (architecture.md
Phase 4, README's last planned feature).

Everything before this endpoint (chat, search, explain, review, debug,
architecture) is a fixed pipeline: a request comes in, a predetermined
sequence of steps runs, one response comes out. This is different - the
LLM itself decides, one step at a time, which tool to call and when it
has enough information to answer, using LangGraph purely as the control
flow for that loop (a `StateGraph` alternating a "plan" node and an "act"
node) rather than any prebuilt LangChain agent, keeping the same
thin-wrapper style as every other service here: `generate_response`
(Day 17) is still the only thing that talks to Anthropic, no `anthropic`
or `langchain-anthropic` SDK involved.

Tool failures (e.g. the still-open OPENAI_API_KEY gap breaking
search_code) are deliberately caught inside each tool and fed back to
the agent as a normal observation rather than raised - the agent can
then adapt (try something else, or answer with a caveat) the same way a
person would if a tool didn't work, rather than the whole request
failing. Only the core planning LLM being unavailable is a real 503/502,
since without it the agent can't reason about anything at all.
"""

from __future__ import annotations

import json
import re
from typing import Optional, TypedDict
from uuid import UUID

from langgraph.graph import END, StateGraph
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.code_chunk import CodeChunk
from app.models.repository_file import RepositoryFile
from app.services import vector_store
from app.services.code_chunking import reconstruct_file_content
from app.services.embeddings import EmbeddingAPIError, EmbeddingConfigError, generate_embedding
from app.services.llm import LLMAPIError, LLMConfigError, generate_response
from app.services.vector_store import VectorStoreError

MAX_TOOL_CALLS = 4
SEARCH_LIMIT = 5

PLAN_SYSTEM_PROMPT = (
    "You are an autonomous coding assistant working step by step towards a "
    "goal about one specific software repository. You have two tools:\n\n"
    "- search_code(query): semantically search the repository's indexed "
    "code; returns the most relevant snippets with file paths and line "
    "ranges.\n"
    "- explain_file(file_path): get an explanation of what one specific "
    "file (exact path) does.\n\n"
    "On each turn, decide the single next action: call one tool, or finish "
    "with a final answer if you already have enough information. Respond "
    "with ONLY a JSON object, no other text, in one of these two shapes:\n"
    '{"action": "tool", "tool": "search_code", "arguments": {"query": "..."}}\n'
    '{"action": "tool", "tool": "explain_file", "arguments": {"file_path": "..."}}\n'
    '{"action": "finish", "answer": "..."}\n\n'
    "Base your final answer only on what the tools actually returned during "
    "this session - never invent file contents or search results you "
    "didn't see. If every tool available failed or found nothing useful, "
    "say so plainly in your answer rather than guessing."
)


class AgentStep(TypedDict):
    tool: str
    arguments: dict
    summary: str


class AgentState(TypedDict):
    goal: str
    steps: list[AgentStep]
    pending_action: Optional[dict]
    answer: Optional[str]


def _parse_decision(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()

    decision = None
    try:
        decision = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                decision = json.loads(match.group(0))
            except (json.JSONDecodeError, ValueError):
                decision = None

    if not isinstance(decision, dict) or decision.get("action") not in ("tool", "finish"):
        return {"action": "finish", "answer": raw.strip()}
    return decision


async def _tool_search_code(repository_id: UUID, arguments: dict, db: AsyncSession) -> str:
    query = str(arguments.get("query") or "").strip()
    if not query:
        return "search_code error: no query provided"

    try:
        vector = await generate_embedding(query)
    except EmbeddingConfigError as exc:
        return f"search_code unavailable: {exc}"
    except EmbeddingAPIError as exc:
        return f"search_code failed: {exc}"

    try:
        hits = await vector_store.search(vector, repository_id, limit=SEARCH_LIMIT)
    except VectorStoreError as exc:
        return f"search_code failed: {exc}"

    if not hits:
        return "search_code found no relevant code for that query."

    chunk_ids = [UUID(hit["payload"]["code_chunk_id"]) for hit in hits]
    rows = await db.execute(
        select(CodeChunk, RepositoryFile.file_path)
        .join(RepositoryFile, CodeChunk.repository_file_id == RepositoryFile.id)
        .where(CodeChunk.id.in_(chunk_ids))
    )
    by_id = {chunk.id: (chunk, file_path) for chunk, file_path in rows}

    blocks = []
    for hit in hits:
        cid = UUID(hit["payload"]["code_chunk_id"])
        if cid not in by_id:
            continue
        chunk, file_path = by_id[cid]
        blocks.append(f"{file_path} (lines {chunk.start_line}-{chunk.end_line}):\n{chunk.content}")

    return "\n\n".join(blocks) if blocks else "search_code found no relevant code for that query."


async def _tool_explain_file(repository_id: UUID, arguments: dict, db: AsyncSession) -> str:
    file_path = str(arguments.get("file_path") or "").strip()
    if not file_path:
        return "explain_file error: no file_path provided"

    repository_file = await db.scalar(
        select(RepositoryFile).where(
            RepositoryFile.repository_id == repository_id, RepositoryFile.file_path == file_path
        )
    )
    if repository_file is None:
        return f"explain_file error: no file found at path '{file_path}'"

    chunks = (
        await db.scalars(
            select(CodeChunk)
            .where(CodeChunk.repository_file_id == repository_file.id)
            .order_by(CodeChunk.chunk_index)
        )
    ).all()
    if not chunks:
        return f"explain_file error: '{file_path}' has no indexed content"

    content = reconstruct_file_content(chunks)
    system_prompt = (
        "You are a code assistant. Concisely explain what this single file "
        "does in 2-4 sentences, based only on the content given."
    )
    user_prompt = f"File: {file_path}\n\n```\n{content}\n```"
    try:
        return await generate_response(system_prompt, user_prompt)
    except LLMConfigError as exc:
        return f"explain_file unavailable: {exc}"
    except LLMAPIError as exc:
        return f"explain_file failed: {exc}"


def _build_graph(repository_id: UUID, db: AsyncSession):
    async def plan_node(state: AgentState) -> AgentState:
        forced_finish = len(state["steps"]) >= MAX_TOOL_CALLS
        transcript = "\n\n".join(
            f"Step {i + 1}: called {s['tool']}({s['arguments']}) -> {s['summary']}"
            for i, s in enumerate(state["steps"])
        )
        user_prompt = f"Goal: {state['goal']}\n\n"
        user_prompt += f"Steps so far:\n{transcript}" if transcript else "No steps taken yet."
        if forced_finish:
            user_prompt += (
                "\n\nYou have used all available tool calls. Respond now with "
                'the finish action - {"action": "finish", "answer": "..."} - '
                "using only what you've already learned."
            )

        raw = await generate_response(PLAN_SYSTEM_PROMPT, user_prompt)
        decision = _parse_decision(raw)
        if forced_finish and decision.get("action") != "finish":
            decision = {"action": "finish", "answer": raw.strip()}

        if decision["action"] == "finish":
            return {**state, "answer": str(decision.get("answer", raw.strip())), "pending_action": None}
        return {**state, "pending_action": decision}

    async def act_node(state: AgentState) -> AgentState:
        action = state["pending_action"] or {}
        tool = action.get("tool")
        arguments = action.get("arguments") or {}

        if tool == "search_code":
            summary = await _tool_search_code(repository_id, arguments, db)
        elif tool == "explain_file":
            summary = await _tool_explain_file(repository_id, arguments, db)
        else:
            summary = f"Unknown tool '{tool}' - ignored."

        new_step: AgentStep = {"tool": str(tool), "arguments": arguments, "summary": summary}
        return {**state, "steps": state["steps"] + [new_step], "pending_action": None}

    def route_after_plan(state: AgentState) -> str:
        return "end" if state.get("answer") is not None else "act"

    graph = StateGraph(AgentState)
    graph.add_node("plan", plan_node)
    graph.add_node("act", act_node)
    graph.set_entry_point("plan")
    graph.add_conditional_edges("plan", route_after_plan, {"act": "act", "end": END})
    graph.add_edge("act", "plan")
    return graph.compile()


async def run_agent(repository_id: UUID, goal: str, db: AsyncSession) -> AgentState:
    graph = _build_graph(repository_id, db)
    initial_state: AgentState = {
        "goal": goal,
        "steps": [],
        "pending_action": None,
        "answer": None,
    }
    return await graph.ainvoke(initial_state)
