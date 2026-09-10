"""Repository download, file-scanning, chunking, and embedding pipeline
(architecture.md Phase 2).

Downloads a repository's default branch as a tarball from GitHub, extracts
it to a temp directory, scans the resulting file tree, persists per-file
metadata to `repository_files`, splits each text file's content into
`code_chunks`, embeds every chunk (Day 14's `generate_embeddings`), and
upserts the vectors into Qdrant (Day 15's `vector_store`) - setting each
`code_chunks.vector_id` once its vector is stored. Runs inside a Celery
worker (Day 34's `app/tasks.py`) rather than FastAPI's `BackgroundTasks`
(Days 7-33) - this function itself is unchanged either way, since it
already opens its own `AsyncSessionLocal` rather than depending on a
request-scoped session.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tarfile
import tempfile
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy import delete

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.code_chunk import CodeChunk
from app.models.repository import Repository, RepositoryStatus
from app.models.repository_file import RepositoryFile
from app.services import vector_store
from app.services.code_chunking import chunk_file
from app.services.embeddings import generate_embeddings
from app.services.github import GITHUB_API_BASE, parse_github_url

logger = logging.getLogger(__name__)

# Directories that are never useful for code intelligence and are always
# skipped, regardless of depth.
EXCLUDED_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".next",
    ".nuxt",
    "target",
    "vendor",
    ".idea",
    ".vscode",
    "coverage",
    ".turbo",
    ".cache",
    "egg-info",
}

LANGUAGE_BY_EXTENSION = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".scala": "scala",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".sh": "shell",
    ".bash": "shell",
    ".sql": "sql",
    ".html": "html",
    ".css": "css",
    ".scss": "scss",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".md": "markdown",
    ".dockerfile": "dockerfile",
}


class RepositoryIngestionError(Exception):
    pass


def _detect_language(file_path: str) -> str | None:
    name = os.path.basename(file_path)
    if name.lower() == "dockerfile":
        return "dockerfile"
    _, ext = os.path.splitext(name)
    return LANGUAGE_BY_EXTENSION.get(ext.lower())


async def _download_tarball(owner: str, repo: str, ref: str) -> bytes:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "RepoMind-AI",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"

    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/tarball/{ref}"
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
    except httpx.RequestError as exc:
        # Day 42 fixed this same gap in app/services/github.py, embeddings.py,
        # llm.py, and vector_store.py: a connection-level failure (GitHub
        # unreachable, DNS failure, timeout) raises a raw httpx.RequestError
        # with no .status_code to check, distinct from GitHub responding
        # with an error status below. ingest_repository's own broad
        # `except Exception` already prevents this from crashing the worker,
        # but a raw httpx exception's message doesn't name the repository or
        # explain what failed the way RepositoryIngestionError's messages
        # do - converting it keeps every ingestion failure's error_message
        # equally useful, not just the ones already going through this path.
        raise RepositoryIngestionError(
            f"Could not reach GitHub to download tarball for '{owner}/{repo}@{ref}': {exc}"
        ) from exc

    if response.status_code != 200:
        raise RepositoryIngestionError(
            f"Failed to download tarball for '{owner}/{repo}@{ref}': "
            f"GitHub returned {response.status_code}"
        )
    return response.content


def _safe_extract(tar: tarfile.TarFile, dest_dir: str) -> None:
    dest_root = os.path.realpath(dest_dir)
    for member in tar.getmembers():
        if not (member.isfile() or member.isdir() or member.issym()):
            continue
        member_path = os.path.realpath(os.path.join(dest_dir, member.name))
        if not (member_path == dest_root or member_path.startswith(dest_root + os.sep)):
            raise RepositoryIngestionError(f"Unsafe path in archive: {member.name}")
    tar.extractall(dest_dir)


def _extract_tarball(data: bytes, dest_dir: str) -> str:
    tmp_tar_path = os.path.join(dest_dir, "_archive.tar.gz")
    with open(tmp_tar_path, "wb") as fh:
        fh.write(data)

    extract_dir = os.path.join(dest_dir, "extracted")
    os.makedirs(extract_dir, exist_ok=True)
    with tarfile.open(tmp_tar_path, mode="r:gz") as tar:
        _safe_extract(tar, extract_dir)
    os.remove(tmp_tar_path)

    entries = os.listdir(extract_dir)
    if len(entries) != 1:
        raise RepositoryIngestionError(
            "Unexpected tarball layout: expected a single top-level directory"
        )
    return os.path.join(extract_dir, entries[0])


def _scan_files(root_dir: str) -> list[dict]:
    max_file_size_bytes = settings.max_file_size_kb * 1024
    results: list[dict] = []

    for current_dir, dirnames, filenames in os.walk(root_dir):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIR_NAMES]

        for filename in filenames:
            abs_path = os.path.join(current_dir, filename)
            try:
                size_bytes = os.path.getsize(abs_path)
            except OSError:
                continue
            if size_bytes > max_file_size_bytes:
                continue

            rel_path = os.path.relpath(abs_path, root_dir).replace(os.sep, "/")

            hasher = hashlib.sha256()
            try:
                with open(abs_path, "rb") as fh:
                    for chunk in iter(lambda: fh.read(65536), b""):
                        hasher.update(chunk)
            except OSError:
                continue

            results.append(
                {
                    "abs_path": abs_path,
                    "file_path": rel_path,
                    "language": _detect_language(rel_path),
                    "size_bytes": size_bytes,
                    "content_hash": hasher.hexdigest(),
                }
            )

    return results


async def ingest_repository(repository_id: uuid.UUID) -> None:
    async with AsyncSessionLocal() as db:
        repository = await db.get(Repository, repository_id)
        if repository is None:
            logger.warning("ingest_repository: repository %s not found", repository_id)
            return

        temp_dir = tempfile.mkdtemp(prefix="repomind-")
        try:
            owner, repo = parse_github_url(repository.github_url)
            ref = repository.default_branch or "HEAD"

            repository.status = RepositoryStatus.CLONING
            await db.commit()

            tarball = await _download_tarball(owner, repo, ref)
            extracted_root = _extract_tarball(tarball, temp_dir)

            repository.status = RepositoryStatus.PROCESSING
            await db.commit()

            scanned_files = _scan_files(extracted_root)

            await db.execute(delete(RepositoryFile).where(RepositoryFile.repository_id == repository.id))
            # Clears any vectors left over from a previous run of this
            # repository (reindex) before new ones are upserted below - the
            # chunks recreated on each run get fresh UUIDs, so stale points
            # from an earlier run would otherwise never be cleaned up.
            await vector_store.delete_by_repository(repository.id)

            pending_chunks: list[CodeChunk] = []
            chunk_total = 0
            for file_data in scanned_files:
                abs_path = file_data.pop("abs_path")

                repository_file = RepositoryFile(repository_id=repository.id, **file_data)
                db.add(repository_file)
                await db.flush()

                try:
                    with open(abs_path, "rb") as fh:
                        raw = fh.read()
                except OSError:
                    continue

                chunks = chunk_file(
                    file_data["file_path"],
                    raw,
                    max_lines=settings.chunk_max_lines,
                    overlap_lines=settings.chunk_overlap_lines,
                )
                for chunk_data in chunks:
                    chunk = CodeChunk(
                        id=uuid.uuid4(),
                        repository_id=repository.id,
                        repository_file_id=repository_file.id,
                        **chunk_data,
                    )
                    db.add(chunk)
                    pending_chunks.append(chunk)
                chunk_total += len(chunks)

            if pending_chunks:
                vectors = await generate_embeddings([chunk.content for chunk in pending_chunks])
                points = [
                    {
                        "id": str(chunk.id),
                        "vector": vector,
                        "payload": {
                            "repository_id": str(repository.id),
                            "code_chunk_id": str(chunk.id),
                            "repository_file_id": str(chunk.repository_file_id),
                            "start_line": chunk.start_line,
                            "end_line": chunk.end_line,
                        },
                    }
                    for chunk, vector in zip(pending_chunks, vectors)
                ]
                await vector_store.upsert_chunks(points)
                for chunk in pending_chunks:
                    chunk.vector_id = str(chunk.id)

            repository.file_count = len(scanned_files)
            repository.total_size_bytes = sum(f["size_bytes"] for f in scanned_files)
            repository.last_indexed_at = datetime.now(timezone.utc)
            repository.status = RepositoryStatus.COMPLETED
            repository.error_message = None
            await db.commit()
            logger.info(
                "ingest_repository: %s scanned %d files, %d chunks, %d embedded",
                repository_id, len(scanned_files), chunk_total, len(pending_chunks),
            )
        except Exception as exc:
            logger.exception("ingest_repository failed for %s", repository_id)
            await db.rollback()
            repository = await db.get(Repository, repository_id)
            if repository is not None:
                repository.status = RepositoryStatus.FAILED
                repository.error_message = str(exc)[:2000]
                await db.commit()
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
