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

Day 32 shipped two tools (search_code, explain_file) as a first slice;
Day 35 added the other four (review_file, debug, architecture,
security_scan), each reusing the same underlying logic as its REST
endpoint counterpart rather than duplicating a second implementation -
just with shorter, tool-appropriate prompts (a few sentences instead of
a full response) so a multi-step transcript doesn't balloon in size.
security_scan needs no LLM or embedding call at all, same as its
standalone endpoint (Day 30).

Day 36 made the step budget (previously a hardcoded MAX_TOOL_CALLS)
into a per-request `max_steps` parameter instead - a goal combining
several of Day 35's tools plausibly needs more than 4 steps, and a
single fixed value can't serve both a quick one-tool lookup and a
longer investigation well. `AgentRequest.max_steps` (bounded 1-10)
threads through `run_agent` down to `_build_graph`'s `plan_node`.
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
from app.services.security_scan import scan_content
from app.services.vector_store import VectorStoreError

# Default step budget when a caller doesn't specify one (Day 36's
# AgentRequest.max_steps, bounded 1-10). Kept as a named constant since
# most callers just want the sensible default rather than picking a
# number every time.
MAX_TOOL_CALLS = 4
SEARCH_LIMIT = 5

PLAN_SYSTEM_PROMPT = (
    "You are an autonomous coding assistant working step by step towards a "
    "goal about one specific software repository. You have these tools:\n\n"
    "- search_code(query): semantically search the repository's indexed "
    "code; returns the most relevant snippets with file paths and line "
    "ranges.\n"
    "- explain_file(file_path): get an explanation of what one specific "
    "file (exact path) does.\n"
    "- review_file(file_path): get a code review of one specific file "
    "(exact path) - bugs, security issues, edge cases, code smells.\n"
    "- debug(description): describe a bug or error; searches the "
    "repository and returns a diagnosis grounded in the matching code.\n"
    "- architecture(): get a high-level overview of the whole "
    "repository's structure, tech stack, and entry points. Takes no "
    "arguments.\n"
    "- security_scan(): run a fast pattern-based scan of the whole "
    "repository for common issues like hardcoded secrets, eval/exec, and "
    "SQL injection risk. Takes no arguments.\n\n"
    "On each turn, decide the single next action: call one tool, or finish "
    "with a final answer if you already have enough information. Respond "
    "with ONLY a JSON object, no other text, giving the tool name and its "
    "arguments (an empty object for tools that take none), or a finish "
    "action:\n"
    '{"action": "tool", "tool": "<tool name>", "arguments": {...}}\n'
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


async def _search_chunks(
    repository_id: UUID, query: str, db: AsyncSession
) -> tuple[list[str], Optional[str]]:
    """Shared by search_code and debug, which both embed a query and search
    Qdrant before diverging (search_code returns the raw snippets, debug
    feeds them to the LLM for a diagnosis). Returns (blocks, error) - on
    any failure or no-match, blocks is empty and error is a short message
    the caller prefixes with its own tool name."""
    try:
        vector = await generate_embedding(query)
    except EmbeddingConfigError as exc:
        return [], f"unavailable: {exc}"
    except EmbeddingAPIError as exc:
        return [], f"failed: {exc}"

    try:
        hits = await vector_store.search(vector, repository_id, limit=SEARCH_LIMIT)
    except VectorStoreError as exc:
        return [], f"failed: {exc}"

    if not hits:
        return [], None

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
    return blocks, None


async def _load_file_content(repository_id: UUID, file_path: str, db: AsyncSession) -> str:
    """Shared by explain_file and review_file, which both need one file's
    reconstructed content before diverging on which prompt to use. Returns
    the content, or a string starting with "error:" the caller checks for
    and returns directly as the tool's observation."""
    if not file_path:
        return "error: no file_path provided"

    repository_file = await db.scalar(
        select(RepositoryFile).where(
            RepositoryFile.repository_id == repository_id, RepositoryFile.file_path == file_path
        )
    )
    if repository_file is None:
        return f"error: no file found at path '{file_path}'"

    chunks = (
        await db.scalars(
            select(CodeChunk)
            .where(CodeChunk.repository_file_id == repository_file.id)
            .order_by(CodeChunk.chunk_index)
        )
    ).all()
    if not chunks:
        return f"error: '{file_path}' has no indexed content"

    return reconstruct_file_content(chunks)


async def _tool_search_code(repository_id: UUID, arguments: dict, db: AsyncSession) -> str:
    query = str(arguments.get("query") or "").strip()
    if not query:
        return "search_code error: no query provided"

    blocks, error = await _search_chunks(repository_id, query, db)
    if error:
        return f"search_code {error}"
    return "\n\n".join(blocks) if blocks else "search_code found no relevant code for that query."


async def _tool_debug(repository_id: UUID, arguments: dict, db: AsyncSession) -> str:
    description = str(arguments.get("description") or "").strip()
    if not description:
        return "debug error: no description provided"

    blocks, error = await _search_chunks(repository_id, description, db)
    if error:
        return f"debug {error}"
    if not blocks:
        return "debug found no relevant code for that description."

    system_prompt = (
        "You are debugging an issue in a specific repository. Given the "
        "retrieved code and a bug description, concisely identify the "
        "likely root cause and a fix in 2-4 sentences, citing files. If "
        "the context isn't enough to diagnose confidently, say so."
    )
    user_prompt = f"Retrieved context:\n\n{chr(10).join(blocks)}\n\nBug description: {description}"
    try:
        return await generate_response(system_prompt, user_prompt)
    except LLMConfigError as exc:
        return f"debug unavailable: {exc}"
    except LLMAPIError as exc:
        return f"debug failed: {exc}"


async def _tool_explain_file(repository_id: UUID, arguments: dict, db: AsyncSession) -> str:
    file_path = str(arguments.get("file_path") or "").strip()
    content = await _load_file_content(repository_id, file_path, db)
    if content.startswith("error:"):
        return f"explain_file {content}"

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


async def _tool_review_file(repository_id: UUID, arguments: dict, db: AsyncSession) -> str:
    file_path = str(arguments.get("file_path") or "").strip()
    content = await _load_file_content(repository_id, file_path, db)
    if content.startswith("error:"):
        return f"review_file {content}"

    system_prompt = (
        "You are a senior software engineer reviewing a single source "
        "file. Concisely point out real bugs, security issues, edge "
        "cases, and code smells in 2-4 sentences, based only on the "
        "content given. If the file looks clean, say so plainly."
    )
    user_prompt = f"File: {file_path}\n\n```\n{content}\n```"
    try:
        return await generate_response(system_prompt, user_prompt)
    except LLMConfigError as exc:
        return f"review_file unavailable: {exc}"
    except LLMAPIError as exc:
        return f"review_file failed: {exc}"


async def _tool_architecture(repository_id: UUID, arguments: dict, db: AsyncSession) -> str:
    files = (
        await db.scalars(
            select(RepositoryFile)
            .where(RepositoryFile.repository_id == repository_id)
            .order_by(RepositoryFile.file_path)
        )
    ).all()
    if not files:
        return "architecture error: no files available to analyze"

    readme_file = next(
        (f for f in files if "/" not in f.file_path and f.file_path.lower().startswith("readme")),
        None,
    )
    readme_content = None
    if readme_file is not None:
        readme_chunks = (
            await db.scalars(
                select(CodeChunk)
                .where(CodeChunk.repository_file_id == readme_file.id)
                .order_by(CodeChunk.chunk_index)
            )
        ).all()
        if readme_chunks:
            readme_content = reconstruct_file_content(readme_chunks)

    file_tree = "\n".join(f"{f.file_path} ({f.language or 'unknown'})" for f in files)
    user_prompt = f"File tree:\n{file_tree}"
    if readme_content:
        user_prompt += f"\n\nREADME ({readme_file.file_path}):\n```\n{readme_content}\n```"

    system_prompt = (
        "You are a software architect. From the file tree given (and "
        "README content if present) - nothing else - concisely describe "
        "the repository's likely main components, tech stack, and entry "
        "points in 3-5 sentences. Don't invent anything the file tree and "
        "README don't evidence."
    )
    try:
        return await generate_response(system_prompt, user_prompt)
    except LLMConfigError as exc:
        return f"architecture unavailable: {exc}"
    except LLMAPIError as exc:
        return f"architecture failed: {exc}"


async def _tool_security_scan(repository_id: UUID, arguments: dict, db: AsyncSession) -> str:
    files = (
        await db.scalars(
            select(RepositoryFile)
            .where(RepositoryFile.repository_id == repository_id)
            .order_by(RepositoryFile.file_path)
        )
    ).all()
    if not files:
        return "security_scan error: no files available to scan"

    findings = []
    for repository_file in files:
        chunks = (
            await db.scalars(
                select(CodeChunk)
                .where(CodeChunk.repository_file_id == repository_file.id)
                .order_by(CodeChunk.chunk_index)
            )
        ).all()
        if not chunks:
            continue
        content = reconstruct_file_content(chunks)
        for finding in scan_content(content):
            findings.append(
                f"{repository_file.file_path}:{finding.line} [{finding.severity}] "
                f"{finding.rule_id} - {finding.message}"
            )

    return "\n".join(findings) if findings else "security_scan found no issues."


def _build_graph(repository_id: UUID, db: AsyncSession, max_steps: int):
    async def plan_node(state: AgentState) -> AgentState:
        forced_finish = len(state["steps"]) >= max_steps
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
        elif tool == "review_file":
            summary = await _tool_review_file(repository_id, arguments, db)
        elif tool == "debug":
            summary = await _tool_debug(repository_id, arguments, db)
        elif tool == "architecture":
            summary = await _tool_architecture(repository_id, arguments, db)
        elif tool == "security_scan":
            summary = await _tool_security_scan(repository_id, arguments, db)
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


async def run_agent(
    repository_id: UUID, goal: str, db: AsyncSession, max_steps: int = MAX_TOOL_CALLS
) -> AgentState:
    graph = _build_graph(repository_id, db, max_steps)
    initial_state: AgentState = {
        "goal": goal,
        "steps": [],
        "pending_action": None,
        "answer": None,
    }
    return await graph.ainvoke(initial_state)
