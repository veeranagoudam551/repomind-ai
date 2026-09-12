import math
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import per_user_rate_limit
from app.models.code_chunk import CodeChunk
from app.models.repository import Repository, RepositoryStatus
from app.models.repository_file import RepositoryFile
from app.models.user import User
from app.schemas.repository import (
    AgentRequest,
    AgentResponse,
    AgentStepRead,
    ArchitectureAnalysisResponse,
    CodeChunkRead,
    CodeSearchResult,
    DebugRequest,
    DebugResponse,
    ExplainFileResponse,
    RepositoryCreate,
    RepositoryFileRead,
    RepositoryPage,
    RepositoryRead,
    RepositorySearchRequest,
    ReviewFileResponse,
    SecurityFindingRead,
    SecurityScanResponse,
)
from app.schemas.errors import error_response
from app.services import vector_store
from app.services.agent import run_agent
from app.services.code_chunking import reconstruct_file_content
from app.services.embedding_providers import get_embedding_provider
from app.services.embeddings import EmbeddingAPIError, EmbeddingConfigError
from app.services.github import (
    GitHubAPIError,
    GitHubRepoNotFound,
    InvalidGitHubUrl,
    fetch_repository,
    parse_github_url,
)
from app.services.llm import LLMAPIError, LLMConfigError, generate_response
from app.services.security_scan import scan_content
from app.services.vector_store import VectorStoreError
from app.tasks import ingest_repository_task

_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}

# No router-level `tags=` (Day 49): this one router spans several
# conceptually distinct areas (repository CRUD, file browsing, search, AI
# analysis, the agent), so each route below sets its own explicit tag(s)
# instead of every operation being lumped under one generic tag.
router = APIRouter(prefix="/repositories")

_REPO_NOT_FOUND = error_response("Repository not found or not owned by the current user.")
_UNAUTHORIZED = error_response("Missing, invalid, or expired access token.")

DEFAULT_REPOSITORY_PAGE_SIZE = 10
MAX_REPOSITORY_PAGE_SIZE = 100

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

QUEUE_INGESTION_ERROR_MESSAGE = (
    "Could not queue ingestion: the background worker is unreachable"
)


async def _queue_ingestion(repository: Repository, db: AsyncSession) -> Repository:
    # Day 38: queuing the Celery task can itself fail or block (see
    # celery_app.py) if Redis is unreachable. Broad except is deliberate -
    # the broker transport raises different exception types depending on
    # the failure mode (e.g. redis.exceptions.ConnectionError/TimeoutError,
    # kombu.exceptions.OperationalError) and the point is that none of them
    # should hang or 500 this request. Instead, fail the repository the
    # same way any other ingestion failure does, so the existing
    # `status: "failed"` handling (frontend included) covers this for free.
    try:
        ingest_repository_task.delay(str(repository.id))
    except Exception:
        repository.status = RepositoryStatus.FAILED
        repository.error_message = QUEUE_INGESTION_ERROR_MESSAGE
        await db.commit()
        await db.refresh(repository)
    return repository


ARCHITECTURE_SYSTEM_PROMPT = (
    "You are a software architect giving a newcomer a high-level overview "
    "of a repository. You are given its full file tree (paths and "
    "languages) and, if available, its README content - nothing else, no "
    "other file contents. From the file tree alone, infer the likely main "
    "components/modules from directory structure, the apparent tech stack "
    "from file extensions and config files (e.g. package.json, "
    "pyproject.toml, requirements.txt), and likely entry points from "
    "conventional naming (e.g. main.py, index.ts, app.py). Use the README "
    "for anything it states directly about the project's purpose or "
    "design. Be explicit that structural inferences are based on file "
    "organization, not a reading of the source itself, and don't invent "
    "components or behavior the file tree and README don't evidence."
)


@router.post(
    "",
    response_model=RepositoryRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(per_user_rate_limit("ingestion"))],
    tags=["Repositories"],
    summary="Add a repository for analysis",
    description=(
        "Looks up a public GitHub repository by URL and queues it for "
        "background ingestion (clone, scan, chunk, embed). Returns "
        "immediately with `status: pending`; poll GET /repositories/{id} "
        "for progress."
    ),
    responses={
        400: error_response(
            "Invalid GitHub URL, the repository is private (unsupported), "
            "it exceeds the configured size limit, or it was already added."
        ),
        401: _UNAUTHORIZED,
        404: error_response("The repository was not found on GitHub."),
        429: error_response("Ingestion rate limit exceeded for this user."),
        502: error_response("GitHub's API returned an error or was unreachable."),
    },
)
async def create_repository(
    payload: RepositoryCreate,
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

    return await _queue_ingestion(repository, db)


@router.get(
    "",
    response_model=RepositoryPage,
    tags=["Repositories"],
    summary="List the current user's repositories",
    description="Paginated, newest first. Only returns repositories owned by the current user.",
    responses={401: _UNAUTHORIZED},
)
async def list_repositories(
    page: int = Query(1, ge=1, description="1-indexed page number."),
    page_size: int = Query(
        DEFAULT_REPOSITORY_PAGE_SIZE,
        ge=1,
        le=MAX_REPOSITORY_PAGE_SIZE,
        description=f"Items per page (max {MAX_REPOSITORY_PAGE_SIZE}).",
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    owner_filter = Repository.owner_id == current_user.id

    total = await db.scalar(
        select(func.count()).select_from(Repository).where(owner_filter)
    )

    result = await db.scalars(
        select(Repository)
        .where(owner_filter)
        .order_by(Repository.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )

    # A page past the end of the results isn't an error - e.g. the last
    # repository on page 2 just got deleted - it's just an empty page, same
    # as any other query that happens to match nothing.
    total_pages = math.ceil(total / page_size) if total else 0

    return RepositoryPage(
        items=result.all(),
        page=page,
        page_size=page_size,
        total=total,
        total_pages=total_pages,
        has_next=page < total_pages,
        has_previous=page > 1,
    )


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


@router.get(
    "/{repository_id}",
    response_model=RepositoryRead,
    tags=["Repositories"],
    summary="Get a repository",
    responses={401: _UNAUTHORIZED, 404: _REPO_NOT_FOUND},
)
async def get_repository(
    repository_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await _get_owned_repository(repository_id, db, current_user)


@router.delete(
    "/{repository_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["Repositories"],
    summary="Delete a repository",
    description="Removes the repository and its indexed data, including vectors stored in Qdrant.",
    responses={
        401: _UNAUTHORIZED,
        404: _REPO_NOT_FOUND,
        502: error_response("The vector store returned an error while deleting indexed vectors."),
    },
)
async def delete_repository(
    repository_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    repository = await _get_owned_repository(repository_id, db, current_user)
    # Delete Qdrant vectors before the Postgres row: if this fails, the
    # repository stays around (and deletable again) rather than leaving
    # orphaned vectors with no code_chunks row left to point at them. A
    # VectorStoreError maps to 502 like every other Qdrant-touching
    # endpoint (search/debug) - this one just didn't catch it (Day 39).
    try:
        await vector_store.delete_by_repository(repository.id)
    except VectorStoreError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    await db.delete(repository)
    await db.commit()


IN_PROGRESS_STATUSES = (
    RepositoryStatus.PENDING,
    RepositoryStatus.CLONING,
    RepositoryStatus.PROCESSING,
)


@router.post(
    "/{repository_id}/reindex",
    response_model=RepositoryRead,
    dependencies=[Depends(per_user_rate_limit("ingestion"))],
    tags=["Repositories"],
    summary="Re-queue a repository for ingestion",
    description="Re-runs the clone/scan/chunk/embed pipeline. Rejected if ingestion is already in progress.",
    responses={
        401: _UNAUTHORIZED,
        404: _REPO_NOT_FOUND,
        409: error_response("Ingestion is already in progress for this repository."),
        429: error_response("Ingestion rate limit exceeded for this user."),
    },
)
async def reindex_repository(
    repository_id: UUID,
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
    return await _queue_ingestion(repository, db)


@router.get(
    "/{repository_id}/files",
    response_model=list[RepositoryFileRead],
    tags=["Files"],
    summary="List a repository's indexed files",
    description="Alphabetical by path. Reflects the most recent completed ingestion, if any.",
    responses={401: _UNAUTHORIZED, 404: _REPO_NOT_FOUND},
)
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


_REPO_OR_FILE_NOT_FOUND = error_response(
    "Repository not found/not owned by the current user, or the file was "
    "not found in this repository."
)
_NO_FILE_CONTENT = error_response("The file has no indexed content (ingestion may not be complete).")
_AI_RATE_LIMITED = error_response("AI request rate limit exceeded for this user.")
_LLM_UNAVAILABLE = error_response("The LLM provider is not configured.")
_LLM_UPSTREAM_ERROR = error_response("The LLM provider returned an error or was unreachable.")


@router.post(
    "/{repository_id}/files/{file_id}/explain",
    response_model=ExplainFileResponse,
    dependencies=[Depends(per_user_rate_limit("ai"))],
    tags=["AI Analysis"],
    summary="Explain a single file",
    description="Generates a plain-language explanation of one indexed file, grounded only in its content.",
    responses={
        400: _NO_FILE_CONTENT,
        401: _UNAUTHORIZED,
        404: _REPO_OR_FILE_NOT_FOUND,
        429: _AI_RATE_LIMITED,
        502: _LLM_UPSTREAM_ERROR,
        503: _LLM_UNAVAILABLE,
    },
)
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


@router.post(
    "/{repository_id}/files/{file_id}/review",
    response_model=ReviewFileResponse,
    dependencies=[Depends(per_user_rate_limit("ai"))],
    tags=["AI Analysis"],
    summary="Review a single file",
    description="Generates a code review (bugs, security issues, code smells) of one indexed file.",
    responses={
        400: _NO_FILE_CONTENT,
        401: _UNAUTHORIZED,
        404: _REPO_OR_FILE_NOT_FOUND,
        429: _AI_RATE_LIMITED,
        502: _LLM_UPSTREAM_ERROR,
        503: _LLM_UNAVAILABLE,
    },
)
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


@router.get(
    "/{repository_id}/chunks",
    response_model=list[CodeChunkRead],
    tags=["Files"],
    summary="List a repository's code chunks",
    description=(
        "The chunk-level decomposition each indexed file was split into for "
        "embedding/search. Optionally filtered to a single file."
    ),
    responses={401: _UNAUTHORIZED, 404: _REPO_NOT_FOUND},
)
async def list_repository_chunks(
    repository_id: UUID,
    file_id: Optional[UUID] = Query(None, description="Restrict results to chunks from this file."),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _get_owned_repository(repository_id, db, current_user)
    query = select(CodeChunk).where(CodeChunk.repository_id == repository_id)
    if file_id is not None:
        query = query.where(CodeChunk.repository_file_id == file_id)
    result = await db.scalars(query.order_by(CodeChunk.repository_file_id, CodeChunk.chunk_index))
    return result.all()


@router.post(
    "/{repository_id}/search",
    response_model=list[CodeSearchResult],
    dependencies=[Depends(per_user_rate_limit("ai"))],
    tags=["Search"],
    summary="Semantic code search",
    description=(
        "Embeds the query and returns the most similar indexed code chunks "
        "in this repository, ranked by similarity score (highest first)."
    ),
    responses={
        401: _UNAUTHORIZED,
        404: _REPO_NOT_FOUND,
        429: _AI_RATE_LIMITED,
        502: error_response("The embedding provider or vector store returned an error."),
        503: error_response("The embedding provider is not configured."),
    },
)
async def search_repository(
    repository_id: UUID,
    payload: RepositorySearchRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _get_owned_repository(repository_id, db, current_user)

    try:
        query_vector = await get_embedding_provider().embed_query(payload.query)
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


@router.post(
    "/{repository_id}/debug",
    response_model=DebugResponse,
    dependencies=[Depends(per_user_rate_limit("ai"))],
    tags=["AI Analysis"],
    summary="Diagnose a bug from a description",
    description=(
        "Retrieves indexed code relevant to the bug description and asks "
        "the LLM to diagnose the likely root cause, citing sources. "
        "Returns a fixed explanatory message (still 200) if nothing "
        "relevant is indexed."
    ),
    responses={
        401: _UNAUTHORIZED,
        404: _REPO_NOT_FOUND,
        429: _AI_RATE_LIMITED,
        502: error_response("The embedding provider, vector store, or LLM provider returned an error."),
        503: error_response("The embedding provider or the LLM provider is not configured."),
    },
)
async def debug_repository(
    repository_id: UUID,
    payload: DebugRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _get_owned_repository(repository_id, db, current_user)

    try:
        query_vector = await get_embedding_provider().embed_query(payload.description)
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


@router.post(
    "/{repository_id}/architecture",
    response_model=ArchitectureAnalysisResponse,
    dependencies=[Depends(per_user_rate_limit("ai"))],
    tags=["AI Analysis"],
    summary="Generate a high-level architecture overview",
    description=(
        "Infers likely components, tech stack, and entry points from the "
        "repository's file tree and README (no other file contents), and "
        "asks the LLM to summarize them."
    ),
    responses={
        400: error_response("No indexed files available to analyze (ingestion may not be complete)."),
        401: _UNAUTHORIZED,
        404: _REPO_NOT_FOUND,
        429: _AI_RATE_LIMITED,
        502: _LLM_UPSTREAM_ERROR,
        503: _LLM_UNAVAILABLE,
    },
)
async def analyze_repository_architecture(
    repository_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    repository = await _get_owned_repository(repository_id, db, current_user)

    files = (
        await db.scalars(
            select(RepositoryFile)
            .where(RepositoryFile.repository_id == repository_id)
            .order_by(RepositoryFile.file_path)
        )
    ).all()
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No files available to analyze for this repository",
        )

    readme_file = next(
        (
            f
            for f in files
            if "/" not in f.file_path and f.file_path.lower().startswith("readme")
        ),
        None,
    )
    readme_content: Optional[str] = None
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
    user_prompt = f"Repository: {repository.name}\n\nFile tree:\n{file_tree}"
    if readme_content:
        user_prompt += f"\n\nREADME ({readme_file.file_path}):\n```\n{readme_content}\n```"

    try:
        analysis = await generate_response(ARCHITECTURE_SYSTEM_PROMPT, user_prompt)
    except LLMConfigError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except LLMAPIError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    return ArchitectureAnalysisResponse(
        analysis=analysis,
        file_count=len(files),
        readme_path=readme_file.file_path if readme_file else None,
    )


@router.post(
    "/{repository_id}/security-scan",
    response_model=SecurityScanResponse,
    dependencies=[Depends(per_user_rate_limit("ai"))],
    tags=["AI Analysis"],
    summary="Run a static security scan",
    description=(
        "Runs a local, rule-based static scan (no LLM call) over every "
        "indexed file's content and returns findings sorted by severity."
    ),
    responses={
        400: error_response("No indexed files available to scan (ingestion may not be complete)."),
        401: _UNAUTHORIZED,
        404: _REPO_NOT_FOUND,
        429: _AI_RATE_LIMITED,
    },
)
async def scan_repository_security(
    repository_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _get_owned_repository(repository_id, db, current_user)

    files = (
        await db.scalars(
            select(RepositoryFile)
            .where(RepositoryFile.repository_id == repository_id)
            .order_by(RepositoryFile.file_path)
        )
    ).all()
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No files available to scan for this repository",
        )

    findings: list[SecurityFindingRead] = []
    files_scanned = 0
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

        files_scanned += 1
        content = reconstruct_file_content(chunks)
        for finding in scan_content(content):
            findings.append(
                SecurityFindingRead(
                    file_path=repository_file.file_path,
                    line=finding.line,
                    rule_id=finding.rule_id,
                    severity=finding.severity,
                    message=finding.message,
                    snippet=finding.snippet,
                )
            )

    findings.sort(key=lambda f: (_SEVERITY_ORDER.get(f.severity, 3), f.file_path, f.line))

    return SecurityScanResponse(findings=findings, files_scanned=files_scanned)


@router.post(
    "/{repository_id}/agent",
    response_model=AgentResponse,
    dependencies=[Depends(per_user_rate_limit("agent"))],
    tags=["Agent"],
    summary="Run a multi-step investigation agent",
    description=(
        "Runs a tool-using agent (up to `max_steps` tool calls - search "
        "code, explain/review a file, debug, architecture) toward the "
        "stated goal, returning a final answer plus the sequence of steps "
        "it took."
    ),
    responses={
        401: _UNAUTHORIZED,
        404: _REPO_NOT_FOUND,
        429: error_response("Agent rate limit exceeded for this user."),
        502: _LLM_UPSTREAM_ERROR,
        503: _LLM_UNAVAILABLE,
    },
)
async def run_repository_agent(
    repository_id: UUID,
    payload: AgentRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _get_owned_repository(repository_id, db, current_user)

    try:
        final_state = await run_agent(repository_id, payload.goal, db, max_steps=payload.max_steps)
    except LLMConfigError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except LLMAPIError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    return AgentResponse(
        answer=final_state["answer"] or "",
        steps=[AgentStepRead(**step) for step in final_state["steps"]],
    )
