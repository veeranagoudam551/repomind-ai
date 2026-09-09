"""Integration tests for app.services.ingest_repository itself, rather than
through the API (which stubs it out entirely - see conftest._stub_ingestion).

GitHub's tarball download is mocked (an in-memory tarball built with the
stdlib `tarfile` module stands in for a real archive); embeddings and Qdrant
are mocked the same way they are in test_embeddings.py / test_vector_store.py
since neither a real OPENAI_API_KEY nor a live Qdrant server is available in
this environment. Everything else - extraction, file scanning, chunking, and
the Day 15 wiring that assigns `code_chunks.vector_id` - runs for real
against the test database.
"""

from __future__ import annotations

import io
import tarfile
import uuid

from sqlalchemy import select

from app.models.code_chunk import CodeChunk
from app.models.repository import Repository, RepositoryStatus
from app.models.repository_file import RepositoryFile
from app.models.user import User
from app.services.repository_ingestion import ingest_repository


def _make_tarball(files: dict[str, bytes]) -> bytes:
    """Build a tarball with the single top-level directory GitHub's tarball
    API always produces (`_extract_tarball` requires exactly one), even
    when `files` is empty.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        root_info = tarfile.TarInfo(name="repo-main")
        root_info.type = tarfile.DIRTYPE
        tar.addfile(root_info)
        for rel_path, content in files.items():
            info = tarfile.TarInfo(name=f"repo-main/{rel_path}")
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


async def _make_repository(db_session) -> Repository:
    user = User(email=f"{uuid.uuid4()}@example.com", hashed_password="x")
    db_session.add(user)
    await db_session.flush()

    repository = Repository(
        owner_id=user.id,
        github_url="https://github.com/octocat/Hello-World",
        name="octocat/Hello-World",
        default_branch="main",
        status=RepositoryStatus.PENDING,
    )
    db_session.add(repository)
    await db_session.commit()
    await db_session.refresh(repository)
    return repository


async def test_ingest_repository_embeds_chunks_and_sets_vector_id(db_session, monkeypatch):
    repository = await _make_repository(db_session)

    tarball = _make_tarball({"README.md": b"line one\nline two\nline three\n"})

    async def _fake_download(owner, repo, ref):
        return tarball

    embed_calls = []

    async def _fake_generate_embeddings(texts):
        embed_calls.append(texts)
        return [[float(i), 0.0] for i in range(len(texts))]

    upsert_calls = []

    async def _fake_upsert(points):
        upsert_calls.append(points)

    delete_calls = []

    async def _fake_delete(repository_id):
        delete_calls.append(repository_id)

    monkeypatch.setattr(
        "app.services.repository_ingestion._download_tarball", _fake_download
    )
    monkeypatch.setattr(
        "app.services.repository_ingestion.generate_embeddings", _fake_generate_embeddings
    )
    monkeypatch.setattr(
        "app.services.repository_ingestion.vector_store.upsert_chunks", _fake_upsert
    )
    monkeypatch.setattr(
        "app.services.repository_ingestion.vector_store.delete_by_repository", _fake_delete
    )
    monkeypatch.setattr(
        "app.services.repository_ingestion.AsyncSessionLocal", lambda: db_session
    )

    await ingest_repository(repository.id)

    # ingest_repository's `async with AsyncSessionLocal() as db:` closes (and
    # so expunges) the session on exit - since that session is db_session
    # here, re-fetch rather than refresh() the now-detached local instance.
    repository = await db_session.get(Repository, repository.id)
    assert repository.status == RepositoryStatus.COMPLETED
    assert delete_calls == [repository.id]
    assert len(embed_calls) == 1

    chunks = (
        await db_session.execute(
            select(CodeChunk).where(CodeChunk.repository_id == repository.id)
        )
    ).scalars().all()
    assert len(chunks) == 1
    assert chunks[0].vector_id == str(chunks[0].id)

    assert len(upsert_calls) == 1
    point = upsert_calls[0][0]
    assert point["id"] == str(chunks[0].id)
    assert point["payload"]["repository_id"] == str(repository.id)


async def test_ingest_repository_skips_embedding_when_no_chunks(db_session, monkeypatch):
    repository = await _make_repository(db_session)

    tarball = _make_tarball({})

    async def _fake_download(owner, repo, ref):
        return tarball

    async def _fail_if_called(*args, **kwargs):
        raise AssertionError("should not be called when there are no chunks")

    async def _fake_delete(repository_id):
        return None

    monkeypatch.setattr(
        "app.services.repository_ingestion._download_tarball", _fake_download
    )
    monkeypatch.setattr(
        "app.services.repository_ingestion.generate_embeddings", _fail_if_called
    )
    monkeypatch.setattr(
        "app.services.repository_ingestion.vector_store.upsert_chunks", _fail_if_called
    )
    monkeypatch.setattr(
        "app.services.repository_ingestion.vector_store.delete_by_repository", _fake_delete
    )
    monkeypatch.setattr(
        "app.services.repository_ingestion.AsyncSessionLocal", lambda: db_session
    )

    await ingest_repository(repository.id)

    repository = await db_session.get(Repository, repository.id)
    assert repository.status == RepositoryStatus.COMPLETED


async def test_ingest_repository_fails_when_embedding_errors(db_session, monkeypatch):
    repository = await _make_repository(db_session)

    tarball = _make_tarball({"file.py": b"print('hi')\n"})

    async def _fake_download(owner, repo, ref):
        return tarball

    async def _fake_delete(repository_id):
        return None

    async def _raise_embedding_error(texts):
        raise RuntimeError("embedding provider unreachable")

    monkeypatch.setattr(
        "app.services.repository_ingestion._download_tarball", _fake_download
    )
    monkeypatch.setattr(
        "app.services.repository_ingestion.generate_embeddings", _raise_embedding_error
    )
    monkeypatch.setattr(
        "app.services.repository_ingestion.vector_store.delete_by_repository", _fake_delete
    )
    monkeypatch.setattr(
        "app.services.repository_ingestion.AsyncSessionLocal", lambda: db_session
    )

    await ingest_repository(repository.id)

    repository = await db_session.get(Repository, repository.id)
    assert repository.status == RepositoryStatus.FAILED
    assert "embedding provider unreachable" in repository.error_message
