"""Conversations and the RAG chat endpoint (architecture.md Phase 3).

Completes the RAG pipeline that Day 16's search endpoint started: embed
the question, search Qdrant (filtered by repository), retrieve the
matching code_chunks, build a context block, call the LLM (Day 17), and
persist both turns - returning the grounded answer plus the source files
it was built from.
"""

import uuid
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.code_chunk import CodeChunk
from app.models.conversation import Conversation
from app.models.message import Message, MessageRole
from app.models.repository import Repository
from app.models.repository_file import RepositoryFile
from app.models.user import User
from app.schemas.conversation import (
    ConversationCreate,
    ConversationRead,
    MessageCreate,
    MessageRead,
    MessageSource,
)
from app.services import vector_store
from app.services.embeddings import EmbeddingAPIError, EmbeddingConfigError, generate_embedding
from app.services.llm import LLMAPIError, LLMConfigError, generate_response
from app.services.vector_store import VectorStoreError

router = APIRouter(tags=["conversations"])

SEARCH_LIMIT = 5

SYSTEM_PROMPT = (
    "You are a code assistant answering questions about a specific software "
    "repository. You are given retrieved code snippets as your only source "
    "of truth. Answer strictly from the retrieved context provided in the "
    "user message. If the context doesn't contain enough information to "
    "answer, say so explicitly rather than guessing. Never claim a file or "
    "function exists unless it appears in the retrieved context."
)

NO_CONTEXT_MESSAGE = (
    "I couldn't find any indexed code in this repository relevant to your "
    "question. The repository may not have finished indexing yet, or "
    "nothing in it relates to what you asked."
)


async def _get_owned_repository(repository_id: UUID, db: AsyncSession, current_user: User) -> Repository:
    repository = await db.scalar(
        select(Repository).where(Repository.id == repository_id, Repository.owner_id == current_user.id)
    )
    if repository is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")
    return repository


async def _get_owned_conversation(conversation_id: UUID, db: AsyncSession, current_user: User) -> Conversation:
    conversation = await db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == current_user.id
        )
    )
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return conversation


async def _fetch_chunks(db: AsyncSession, chunk_ids: list[uuid.UUID]):
    if not chunk_ids:
        return {}
    rows = await db.execute(
        select(CodeChunk, RepositoryFile.file_path)
        .join(RepositoryFile, CodeChunk.repository_file_id == RepositoryFile.id)
        .where(CodeChunk.id.in_(chunk_ids))
    )
    return {str(chunk.id): (chunk, file_path) for chunk, file_path in rows}


async def _to_message_read(db: AsyncSession, message: Message) -> MessageRead:
    chunk_ids = message.source_chunk_ids or []
    chunk_by_id = await _fetch_chunks(db, [uuid.UUID(cid) for cid in chunk_ids])
    sources = [
        MessageSource(
            code_chunk_id=chunk.id,
            file_path=file_path,
            start_line=chunk.start_line,
            end_line=chunk.end_line,
        )
        for cid in chunk_ids
        if cid in chunk_by_id
        for chunk, file_path in [chunk_by_id[cid]]
    ]
    return MessageRead(
        id=message.id,
        role=message.role,
        content=message.content,
        sources=sources,
        created_at=message.created_at,
    )


@router.post(
    "/repositories/{repository_id}/conversations",
    response_model=ConversationRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation(
    repository_id: UUID,
    payload: ConversationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _get_owned_repository(repository_id, db, current_user)
    conversation = Conversation(
        repository_id=repository_id, user_id=current_user.id, title=payload.title
    )
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)
    return conversation


@router.get("/repositories/{repository_id}/conversations", response_model=list[ConversationRead])
async def list_conversations(
    repository_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _get_owned_repository(repository_id, db, current_user)
    result = await db.scalars(
        select(Conversation)
        .where(Conversation.repository_id == repository_id, Conversation.user_id == current_user.id)
        .order_by(Conversation.created_at.desc())
    )
    return result.all()


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageRead])
async def list_messages(
    conversation_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    conversation = await _get_owned_conversation(conversation_id, db, current_user)
    result = await db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at)
    )
    return [await _to_message_read(db, message) for message in result.all()]


@router.post("/conversations/{conversation_id}/messages", response_model=MessageRead)
async def send_message(
    conversation_id: UUID,
    payload: MessageCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    conversation = await _get_owned_conversation(conversation_id, db, current_user)

    db.add(Message(conversation_id=conversation.id, role=MessageRole.USER, content=payload.content))
    await db.commit()

    try:
        query_vector = await generate_embedding(payload.content)
    except EmbeddingConfigError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except EmbeddingAPIError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    try:
        hits = await vector_store.search(query_vector, conversation.repository_id, limit=SEARCH_LIMIT)
    except VectorStoreError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    if not hits:
        assistant_message = Message(
            conversation_id=conversation.id,
            role=MessageRole.ASSISTANT,
            content=NO_CONTEXT_MESSAGE,
            source_chunk_ids=[],
        )
        db.add(assistant_message)
        await db.commit()
        await db.refresh(assistant_message)
        return await _to_message_read(db, assistant_message)

    chunk_ids_in_order = [hit["payload"]["code_chunk_id"] for hit in hits]
    chunk_by_id = await _fetch_chunks(db, [uuid.UUID(cid) for cid in chunk_ids_in_order])

    context_blocks = [
        f"File: {chunk_by_id[cid][1]} (lines {chunk_by_id[cid][0].start_line}-{chunk_by_id[cid][0].end_line})\n"
        f"```\n{chunk_by_id[cid][0].content}\n```"
        for cid in chunk_ids_in_order
        if cid in chunk_by_id
    ]
    user_prompt = (
        f"Retrieved context:\n\n{chr(10).join(context_blocks)}\n\nQuestion: {payload.content}"
    )

    try:
        answer = await generate_response(SYSTEM_PROMPT, user_prompt)
    except LLMConfigError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except LLMAPIError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    assistant_message = Message(
        conversation_id=conversation.id,
        role=MessageRole.ASSISTANT,
        content=answer,
        source_chunk_ids=[cid for cid in chunk_ids_in_order if cid in chunk_by_id],
    )
    db.add(assistant_message)
    await db.commit()
    await db.refresh(assistant_message)
    return await _to_message_read(db, assistant_message)
