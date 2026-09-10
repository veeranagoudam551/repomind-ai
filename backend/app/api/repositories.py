from typing import Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.models.code_chunk import CodeChunk
from app.models.repository import Repository, RepositoryStatus
from app.models.repository_file import RepositoryFile
from app.models.user import User
from app.schemas.repository import (
    CodeChunkRead,
    CodeSearchResult,
    DebugRequest,
    DebugResponse,
    ExplainFileResponse,
    RepositoryCreate,
    RepositoryFileRead,
    RepositoryRead,
    RepositorySearchRequest,
    ReviewFileResponse,
)
from app.services import vector_store
from app.services.code_chunking import reconstruct_file_content
from app.services.embeddings import EmbeddingAPIError, EmbeddingConfigError, generate_embedding
from app.services.github import (
    GitHubAPIError,
    GitHubRepoNotFound,
    InvalidGitHubUrl,
    fetch_repository,
    parse_github_url,
)
from app.services.llm import LLMAPIError, LLMConfigError, generate_response
from app.services.repository_ingestion import ingest_repository
from app.services.vector_store import VectorStoreError

router = APIRouter(prefix="/repositories", tags=["repositories"])

DEBUG_SEARCH_LIMIT = 5

DEBUG_SYSTEM_PROMPT = (
    "You are a senior software engineer helping debug an issue in a "
    "specific repository. You are given retrieved code snippets as your "
    "only source of truth, plus a description of the bug or error. "
    "Identify the likely root cause and suggest a concrete fix, citing "
    "specific files, functions, or lines from the retrieved context. If "
    "the context doesn't contain enough information to diagnose the issue "
    "confidently, say so explicitly rather than guessing. Never claim a "
    "file or function exists unless it appears in the retrieved context."
)

DEBUG_NO_CONTEXT_MESSAGE = (
    "I couldn't find any indexed code in this repository relevant to this "
    "description. The repository may not have finished indexing yet, or "
    "nothing in it relates to the error described."
)


@router.post("", response_model=RepositoryRead, status_code=status.HTTP_201_CREATED)
async def create_repository(
    payload: RepositoryCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        owner, repo = parse_github_url(payload.github_url)
    except InvalidGitHubUrl as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    try:
        repo_info = await fetch_repository(owner, repo)
    except GitHubRepoNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except GitHubAPIError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    if repo_info.private:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Private repositories are not supported yet",
        )

    size_mb = repo_info.size_kb / 1024
    if size_mb > settings.max_repo_size_mb:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Repository is {size_mb:.1f} MB, which exceeds the "
                f"{settings.max_repo_size_mb} MB limit"
            ),
        )

    existing = await db.scalar(
        select(Repository).where(
            Repository.owner_id == current_user.id,
            Repository.github_url == repo_info.html_url,
        )
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You have already added this repository",
        )

    repository = Repository(
        owner_id=current_user.id,
        github_url=repo_info.html_url,
        name=repo_info.full_name,
        description=repo_info.description,
        default_branch=repo_info.default_branch,
        status=RepositoryStatus.PENDING,
        total_size_bytes=repo_info.size_kb * 1024,
    )
    db.add(repository)
    await db.commit()
    await db.refresh(repository)

    background_tasks.add_task(ingest_repository, repository.id)

    return repository


@router.get("", response_model=list[RepositoryRead])
async def list_repositories(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.scalars(
        select(Repository)
        .where(Repository.owner_id == current_user.id)
        .order_by(Repository.created_at.desc())
    )
    return result.all()


async def _get_owned_repository(
    repository_id: UUID, db: AsyncSession, current_user: User
) -> Repository:
    repository = await db.scalar(
        select(Repository).where(
            Repository.id == repository_id, Repository.owner_id == current_user.id
        )
    )
    if repository is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")
    return repository


@router.get("/{repository_id}", response_model=RepositoryRead)
async def get_repository(
    repository_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await _get_owned_repository(repository_id, db, current_user)


@router.delete("/{repository_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_repository(
    repository_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    repository = await _get_owned_repository(repository_id, db, current_user)
    # Delete Qdrant vectors before the Postgres row: if this fails, the
    # repository stays around (and deletable again) rather than leaving
    # orphaned vectors with no code_chunks row left to point at them.
    await vector_store.delete_by_repository(repository.id)
    await db.delete(repository)
    await db.commit()


IN_PROGRESS_STATUSES = (
    RepositoryStatus.PENDING,
    RepositoryStatus.CLONING,
    RepositoryStatus.PROCESSING,
)


@router.post("/{repository_id}/reindex", response_model=RepositoryRead)
async def reindex_repository(
    repository_id: UUID,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Atomically claim the job: only succeeds if no ingestion is already
    # in flight, so two concurrent reindex calls can't both start one.
    result = await db.execute(
        update(Repository)
        .where(
            Repository.id == repository_id,
            Repository.owner_id == current_user.id,
            Repository.status.notin_(IN_PROGRESS_STATUSES),
        )
        .values(status=RepositoryStatus.PENDING, error_message=None)
        .returning(Repository.id)
    )
    updated_id = result.scalar_one_or_none()
    await db.commit()

    if updated_id is None:
        await _get_owned_repository(repository_id, db, current_user)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Repository ingestion is already in progress",
        )

    repository = await db.get(Repository, updated_id)
    background_tasks.add_task(ingest_repository, repository.id)
    return repository


@router.get("/{repository_id}/files", response_model=list[RepositoryFileRead])
async def list_repository_files(
    repository_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _get_owned_repository(repository_id, db, current_user)
    result = await db.scalars(
        select(RepositoryFile)
        .where(RepositoryFile.repository_id == repository_id)
        .order_by(RepositoryFile.file_path)
    )
    return result.all()


async def _get_file_content_for_llm(
    repository_id: UUID, file_id: UUID, db: AsyncSession
) -> tuple[RepositoryFile, str]:
    repository_file = await db.scalar(
        select(RepositoryFile).where(
            RepositoryFile.id == file_id, RepositoryFile.repository_id == repository_id
        )
    )
    if repository_file is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    chunks = (
        await db.scalars(
            select(CodeChunk)
            .where(CodeChunk.repository_file_id == file_id)
            .order_by(CodeChunk.chunk_index)
        )
    ).all()
    if not chunks:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No content available for this file",
        )

    return repository_file, reconstruct_file_content(chunks)


@router.post("/{repository_id}/files/{file_id}/explain", response_model=ExplainFileResponse)
async def explain_repository_file(
    repository_id: UUID,
    file_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _get_owned_repository(repository_id, db, current_user)
    repository_file, content = await _get_file_content_for_llm(repository_id, file_id, db)

    system_prompt = (
        "You are a code assistant explaining a single source file from a "
        "software repository to a developer. Describe what it does, its "
        "key functions/classes/exports, and anything a newcomer to the "
        "codebase would need to know. Base your explanation only on the "
        "file content given; don't invent behavior it doesn't show."
    )
    user_prompt = f"File: {repository_file.file_path}\n\n```\n{content}\n```"

    try:
        explanation = await generate_response(system_prompt, user_prompt)
    except LLMConfigError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except LLMAPIError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    return ExplainFileResponse(file_path=repository_file.file_path, explanation=explanation)


@router.post("/{repository_id}/files/{file_id}/review", response_model=ReviewFileResponse)
async def review_repository_file(
    repository_id: UUID,
    file_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _get_owned_repository(repository_id, db, current_user)
    repository_file, content = await _get_file_content_for_llm(repository_id, file_id, db)

    system_prompt = (
        "You are a senior software engineer performing a code review of a "
        "single source file from a repository. Point out real bugs, "
        "security issues, edge cases, and code smells, and suggest "
        "concrete improvements. Reference specific lines or symbols where "
        "possible. Base your review only on the file content given; don't "
        "invent behavior it doesn't show, and don't invent issues in code "
        "that isn't there. If the file looks clean, say so plainly rather "
        "than manufacturing nitpicks."
    )
    user_prompt = f"File: {repository_file.file_path}\n\n```\n{content}\n```"

    try:
        review = await generate_response(system_prompt, user_prompt)
    except LLMConfigError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except LLMAPIError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    return ReviewFileResponse(file_path=repository_file.file_path, review=review)


@router.get("/{repository_id}/chunks", response_model=list[CodeChunkRead])
async def list_repository_chunks(
    repository_id: UUID,
    file_id: Optional[UUID] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _get_owned_repository(repository_id, db, current_user)
    query = select(CodeChunk).where(CodeChunk.repository_id == repository_id)
    if file_id is not None:
        query = query.where(CodeChunk.repository_file_id == file_id)
    result = await db.scalars(query.order_by(CodeChunk.repository_file_id, CodeChunk.chunk_index))
    return result.all()


@router.post("/{repository_id}/search", response_model=list[CodeSearchResult])
async def search_repository(
    repository_id: UUID,
    payload: RepositorySearchRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _get_owned_repository(repository_id, db, current_user)

    try:
        query_vector = await generate_embedding(payload.query)
    except EmbeddingConfigError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except EmbeddingAPIError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    try:
        hits = await vector_store.search(query_vector, repository_id, limit=payload.limit)
    except VectorStoreError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    if not hits:
        return []

    score_by_chunk_id = {UUID(hit["payload"]["code_chunk_id"]): hit["score"] for hit in hits}

    rows = await db.execute(
        select(CodeChunk, RepositoryFile.file_path)
        .join(RepositoryFile, CodeChunk.repository_file_id == RepositoryFile.id)
        .where(CodeChunk.id.in_(score_by_chunk_id.keys()))
    )

    results = [
        CodeSearchResult(
            code_chunk_id=chunk.id,
            file_path=file_path,
            content=chunk.content,
            start_line=chunk.start_line,
            end_line=chunk.end_line,
            score=score_by_chunk_id[chunk.id],
        )
        for chunk, file_path in rows
    ]
    results.sort(key=lambda r: r.score, reverse=True)
    return results


@router.post("/{repository_id}/debug", response_model=DebugResponse)
async def debug_repository(
    repository_id: UUID,
    payload: DebugRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _get_owned_repository(repository_id, db, current_user)

    try:
        query_vector = await generate_embedding(payload.description)
    except EmbeddingConfigError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except EmbeddingAPIError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    try:
        hits = await vector_store.search(query_vector, repository_id, limit=DEBUG_SEARCH_LIMIT)
    except VectorStoreError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    if not hits:
        return DebugResponse(diagnosis=DEBUG_NO_CONTEXT_MESSAGE, sources=[])

    score_by_chunk_id = {UUID(hit["payload"]["code_chunk_id"]): hit["score"] for hit in hits}

    rows = await db.execute(
        select(CodeChunk, RepositoryFile.file_path)
        .join(RepositoryFile, CodeChunk.repository_file_id == RepositoryFile.id)
        .where(CodeChunk.id.in_(score_by_chunk_id.keys()))
    )

    sources = [
        CodeSearchResult(
            code_chunk_id=chunk.id,
            file_path=file_path,
            content=chunk.content,
            start_line=chunk.start_line,
            end_line=chunk.end_line,
            score=score_by_chunk_id[chunk.id],
        )
        for chunk, file_path in rows
    ]
    sources.sort(key=lambda r: r.score, reverse=True)

    context_blocks = [
        f"File: {source.file_path} (lines {source.start_line}-{source.end_line})\n"
        f"```\n{source.content}\n```"
        for source in sources
    ]
    user_prompt = (
        f"Retrieved context:\n\n{chr(10).join(context_blocks)}\n\n"
        f"Bug description: {payload.description}"
    )

    try:
        diagnosis = await generate_response(DEBUG_SYSTEM_PROMPT, user_prompt)
    except LLMConfigError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except LLMAPIError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    return DebugResponse(diagnosis=diagnosis, sources=sources)
