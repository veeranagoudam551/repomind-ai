from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.models.repository import Repository, RepositoryStatus
from app.models.repository_file import RepositoryFile
from app.models.user import User
from app.schemas.repository import RepositoryCreate, RepositoryFileRead, RepositoryRead
from app.services.github import (
    GitHubAPIError,
    GitHubRepoNotFound,
    InvalidGitHubUrl,
    fetch_repository,
    parse_github_url,
)
from app.services.repository_ingestion import ingest_repository

router = APIRouter(prefix="/repositories", tags=["repositories"])


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
