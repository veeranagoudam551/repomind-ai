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

import httpx
import pytest
from sqlalchemy import select

from app.models.code_chunk import CodeChunk
from app.models.repository import Repository, RepositoryStatus
from app.models.repository_file import RepositoryFile
from app.models.user import User
from app.services.repository_ingestion import (
    RepositoryIngestionError,
    _download_tarball,
    ingest_repository,
)


def _install_mock_transport(monkeypatch, handler):
    # Same technique as test_vector_store.py/test_embeddings.py/test_llm.py/
    # test_github.py (Day 42): a handler that raises instead of returning a
    # Response simulates a connection-level failure, not a bad HTTP status.
    transport = httpx.MockTransport(handler)

    class FakeAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("app.services.repository_ingestion.httpx.AsyncClient", FakeAsyncClient)


def _make_tarball(files: dict[str, bytes]) -> bytes:
    """Build a tarball with the single top-level directory GitHub's tarball
    API always produces (`_extract_tarball` requires exactly one), even
    when `files` is empty.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        root_info = tarfile.TarInfo(name="repo-main")
        root_info.type = tarfile.DIRTYPE
        # TarInfo() defaults to mode 0o644 regardless of type - fine for a
        # file, but a directory needs the execute/search bit too (POSIX:
        # read lets you list a directory's names, execute lets you actually
        # enter it) or it's non-traversable once extracted. os.walk() then
        # silently finds nothing (its default onerror swallows the
        # PermissionError on the very first scandir), so _scan_files()
        # returns zero files with no error at all - passed unnoticed on
        # Windows, which doesn't enforce this POSIX permission model, until
        # Day 44's Linux CI run actually caught it (0 chunks embedded where
        # 1 was expected, and a simulated embedding failure never firing
        # since the empty-chunks guard skips calling it at all). Real
        # GitHub tarballs always have sane, traversable directory
        # permissions - this was purely a synthetic-fixture gap, not
        # anything wrong with repository_ingestion.py itself.
        root_info.mode = 0o755
        tar.addfile(root_info)
        for rel_path, content in files.items():
            info = tarfile.TarInfo(name=f"repo-main/{rel_path}")
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def test_make_tarball_root_directory_is_traversable():
    # Regression test for the Day 44 Linux-CI-only failure this fixture
    # caused (see docs/architecture.md's Day 44 entry for the full
    # writeup). Deliberately checks the mode *byte written into the tar
    # stream* rather than actually extracting and walking it: the byte is
    # what's wrong regardless of platform, but the resulting failure (an
    # unreadable directory) only ever manifests on POSIX, so an
    # extract-and-check test would pass on this Windows dev machine even
    # with the bug reintroduced - exactly how it went unnoticed for 45
    # days. Checking the byte directly makes this test actually catch a
    # regression here, not just on whichever CI runner happens to be Linux.
    data = _make_tarball({"README.md": b"hello\n"})
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        root = tar.getmember("repo-main")
    assert root.isdir()
    assert root.mode & 0o100, (
        f"root directory mode {oct(root.mode)} is missing the owner execute "
        "bit - it would extract non-traversable on Linux even though "
        "nothing catches it on Windows"
    )


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


async def test_download_tarball_raises_ingestion_error_on_connection_failure(monkeypatch):
    # Day 43: a fully unreachable GitHub (DNS failure, connection refused,
    # timeout) raises a raw httpx.RequestError with no .status_code to
    # check - this must become a RepositoryIngestionError with a useful,
    # contextualized message, the same fix Day 42 applied to the sibling
    # service clients.
    def handler(request):
        raise httpx.ConnectError("Connection refused", request=request)

    _install_mock_transport(monkeypatch, handler)

    with pytest.raises(RepositoryIngestionError, match="octocat/Hello-World@main"):
        await _download_tarball("octocat", "Hello-World", "main")


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


async def test_ingest_repository_fails_with_useful_message_when_github_unreachable(
    db_session, monkeypatch
):
    # Day 43: unlike the tests above, this doesn't monkeypatch
    # _download_tarball away - it lets the real function (and its Day 43
    # try/except httpx.RequestError) run against a mocked transport, so the
    # whole real code path from ingest_repository down through the actual
    # httpx call is exercised, not a stand-in. Confirms the task ends in a
    # clean FAILED with a message naming the repository, not an unhandled
    # exception or a bare, uncontextualized httpx error string.
    repository = await _make_repository(db_session)

    def handler(request):
        raise httpx.ConnectError("Connection refused", request=request)

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.repository_ingestion.AsyncSessionLocal", lambda: db_session)

    await ingest_repository(repository.id)

    repository = await db_session.get(Repository, repository.id)
    assert repository.status == RepositoryStatus.FAILED
    assert "Could not reach GitHub" in repository.error_message
    assert "octocat/Hello-World@main" in repository.error_message
