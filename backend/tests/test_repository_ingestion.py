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
import logging
import re
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
    _is_excluded_filename,
    _scan_files,
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

    class _FakeEmbeddingProvider:
        async def embed_texts(self, texts):
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
        "app.services.repository_ingestion.get_embedding_provider",
        lambda: _FakeEmbeddingProvider(),
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


async def test_ingest_repository_logs_start_and_duration_on_success(
    db_session, monkeypatch, caplog
):
    # Day 59: proves a worker picking up the task is visible in logs even
    # before anything else happens, and that the eventual success log
    # records how long the whole run took - deterministic (any
    # non-negative duration matches the pattern below), not a timing
    # assertion on a specific value.
    caplog.set_level(logging.INFO, logger="app.services.repository_ingestion")

    repository = await _make_repository(db_session)
    tarball = _make_tarball({"README.md": b"line one\nline two\n"})

    async def _fake_download(owner, repo, ref):
        return tarball

    class _FakeEmbeddingProvider:
        async def embed_texts(self, texts):
            return [[0.0, 0.0] for _ in texts]

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr("app.services.repository_ingestion._download_tarball", _fake_download)
    monkeypatch.setattr(
        "app.services.repository_ingestion.get_embedding_provider",
        lambda: _FakeEmbeddingProvider(),
    )
    monkeypatch.setattr("app.services.repository_ingestion.vector_store.upsert_chunks", _noop)
    monkeypatch.setattr(
        "app.services.repository_ingestion.vector_store.delete_by_repository", _noop
    )
    monkeypatch.setattr(
        "app.services.repository_ingestion.AsyncSessionLocal", lambda: db_session
    )

    await ingest_repository(repository.id)

    start_records = [r for r in caplog.records if "starting for" in r.getMessage()]
    assert len(start_records) == 1
    assert str(repository.id) in start_records[0].getMessage()

    success_records = [r for r in caplog.records if "embedded in" in r.getMessage()]
    assert len(success_records) == 1
    assert re.search(r"embedded in \d+\.\d+s", success_records[0].getMessage())


async def test_ingest_repository_logs_start_and_duration_on_failure(
    db_session, monkeypatch, caplog
):
    caplog.set_level(logging.INFO, logger="app.services.repository_ingestion")

    repository = await _make_repository(db_session)
    tarball = _make_tarball({"file.py": b"print('hi')\n"})

    async def _fake_download(owner, repo, ref):
        return tarball

    async def _fake_delete(repository_id):
        return None

    async def _raise_embedding_error(texts):
        raise RuntimeError("embedding provider unreachable")

    class _RaisingEmbeddingProvider:
        embed_texts = staticmethod(_raise_embedding_error)

    monkeypatch.setattr("app.services.repository_ingestion._download_tarball", _fake_download)
    monkeypatch.setattr(
        "app.services.repository_ingestion.get_embedding_provider",
        lambda: _RaisingEmbeddingProvider(),
    )
    monkeypatch.setattr(
        "app.services.repository_ingestion.vector_store.delete_by_repository", _fake_delete
    )
    monkeypatch.setattr(
        "app.services.repository_ingestion.AsyncSessionLocal", lambda: db_session
    )

    await ingest_repository(repository.id)

    start_records = [r for r in caplog.records if "starting for" in r.getMessage()]
    assert len(start_records) == 1

    failure_records = [r for r in caplog.records if "ingest_repository failed for" in r.getMessage()]
    assert len(failure_records) == 1
    assert re.search(r"failed for .+ after \d+\.\d+s", failure_records[0].getMessage())

    # No sensitive data (the fake exception's own message is a plain,
    # non-secret string here) - this is the same log line that
    # tests/test_secret_safety.py already checks more broadly across the
    # whole app; this test only adds the duration-logging assertion.
    assert "embedding provider unreachable" not in failure_records[0].getMessage()


async def test_ingest_repository_skips_embedding_when_no_chunks(db_session, monkeypatch):
    repository = await _make_repository(db_session)

    tarball = _make_tarball({})

    async def _fake_download(owner, repo, ref):
        return tarball

    async def _fail_if_called(*args, **kwargs):
        raise AssertionError("should not be called when there are no chunks")

    class _FailingEmbeddingProvider:
        embed_texts = staticmethod(_fail_if_called)

    async def _fake_delete(repository_id):
        return None

    monkeypatch.setattr(
        "app.services.repository_ingestion._download_tarball", _fake_download
    )
    monkeypatch.setattr(
        "app.services.repository_ingestion.get_embedding_provider",
        lambda: _FailingEmbeddingProvider(),
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

    class _RaisingEmbeddingProvider:
        embed_texts = staticmethod(_raise_embedding_error)

    monkeypatch.setattr(
        "app.services.repository_ingestion._download_tarball", _fake_download
    )
    monkeypatch.setattr(
        "app.services.repository_ingestion.get_embedding_provider",
        lambda: _RaisingEmbeddingProvider(),
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


# --- Secret-bearing filename exclusion (Phase 4, Part A) -------------------


@pytest.mark.parametrize(
    "filename",
    [
        ".env",
        ".env.production",
        ".env.local",
        ".env.development",
        ".env.test",
        ".ENV.PRODUCTION",  # case-insensitive
        "server.pem",
        "private.key",
        "id_rsa",
        "id_ed25519",
        "id_ecdsa",
        "credentials.json",
        "service-account.json",
        "serviceAccountKey.json",
        "my-project-service_account.json",
        ".npmrc",
        ".pypirc",
    ],
)
def test_is_excluded_filename_excludes_known_secret_patterns(filename):
    assert _is_excluded_filename(filename) is True


@pytest.mark.parametrize(
    "filename",
    [
        ".env.example",
        ".env.sample",
        ".env.template",
        ".ENV.EXAMPLE",  # case-insensitive
        "config.py",
        "settings.py",
        "application.yml",
        "package.json",
        "package-lock.json",
        "tsconfig.json",
        "docker-compose.json",
        "keystore_config.py",
        "credentials_model.py",
        "environment.py",
        "account_service.py",  # "account" + "service" present but not the json pattern
        "README.md",
    ],
)
def test_is_excluded_filename_keeps_legitimate_files(filename):
    assert _is_excluded_filename(filename) is False


def test_scan_files_excludes_secrets_but_keeps_normal_files(tmp_path):
    (tmp_path / ".env").write_text("SECRET=1\n")
    (tmp_path / ".env.production").write_text("SECRET=2\n")
    (tmp_path / ".env.example").write_text("SECRET=changeme\n")
    (tmp_path / "id_rsa").write_text("-----BEGIN OPENSSH PRIVATE KEY-----\n")
    (tmp_path / "server.pem").write_text("-----BEGIN CERTIFICATE-----\n")
    (tmp_path / "credentials.json").write_text('{"key": "secret"}\n')
    (tmp_path / ".npmrc").write_text("//registry.npmjs.org/:_authToken=abc\n")
    backend_dir = tmp_path / "backend"
    backend_dir.mkdir()
    (backend_dir / ".env").write_text("NESTED_SECRET=1\n")
    (tmp_path / "config.py").write_text("DEBUG = True\n")
    (tmp_path / "package.json").write_text('{"name": "demo"}\n')

    scanned = _scan_files(str(tmp_path))
    scanned_paths = {f["file_path"] for f in scanned}

    assert scanned_paths == {"config.py", "package.json", ".env.example"}


# --- End-to-end: excluded files never reach repository_files/chunks/ -------
# --- embedding/Qdrant (Phase 4, Part A, requirement 4) ----------------------


async def test_ingest_repository_never_persists_or_embeds_a_secret_file(db_session, monkeypatch):
    repository = await _make_repository(db_session)

    tarball = _make_tarball(
        {
            "backend/.env": b"DB_PASSWORD=reallysecret\n",
            "backend/app/main.py": b"def create_app():\n    return {}\n",
        }
    )

    async def _fake_download(owner, repo, ref):
        return tarball

    embed_calls: list[list[str]] = []

    class _FakeEmbeddingProvider:
        async def embed_texts(self, texts):
            embed_calls.append(list(texts))
            return [[0.0, 0.0] for _ in texts]

    upsert_calls: list[list[dict]] = []

    async def _fake_upsert(points):
        upsert_calls.append(points)

    async def _fake_delete(repository_id):
        return None

    monkeypatch.setattr("app.services.repository_ingestion._download_tarball", _fake_download)
    monkeypatch.setattr(
        "app.services.repository_ingestion.get_embedding_provider",
        lambda: _FakeEmbeddingProvider(),
    )
    monkeypatch.setattr("app.services.repository_ingestion.vector_store.upsert_chunks", _fake_upsert)
    monkeypatch.setattr(
        "app.services.repository_ingestion.vector_store.delete_by_repository", _fake_delete
    )
    monkeypatch.setattr("app.services.repository_ingestion.AsyncSessionLocal", lambda: db_session)

    await ingest_repository(repository.id)

    repository = await db_session.get(Repository, repository.id)
    assert repository.status == RepositoryStatus.COMPLETED
    # Only the legitimate file was scanned/counted.
    assert repository.file_count == 1

    files = (
        await db_session.execute(
            select(RepositoryFile).where(RepositoryFile.repository_id == repository.id)
        )
    ).scalars().all()
    file_paths = {f.file_path for f in files}
    assert file_paths == {"backend/app/main.py"}
    assert ".env" not in " ".join(file_paths)

    # No chunk anywhere carries the secret file's content.
    chunks = (
        await db_session.execute(select(CodeChunk).where(CodeChunk.repository_id == repository.id))
    ).scalars().all()
    for chunk in chunks:
        assert "reallysecret" not in chunk.content

    # The embedding provider and Qdrant upsert never even saw the
    # secret's content - not just "it got filtered out of what got
    # stored", but "it was never sent anywhere outside this process".
    all_embedded_texts = [text for call in embed_calls for text in call]
    assert not any("reallysecret" in text for text in all_embedded_texts)
    all_upserted_points = [point for call in upsert_calls for point in call]
    assert len(all_upserted_points) == len(chunks)


# --- Re-ingestion already removes stale files/chunks/vectors ---------------
# (Phase 4, Part A: "inspect whether an already-ingested secret file can
# remain in the database/Qdrant after a re-ingestion"). Verified here
# directly against real database behavior (code_chunks.repository_file_id
# has a real ON DELETE CASCADE - see alembic/versions/7fa9ae014d2e - and
# ingest_repository() already deletes every repository_files row plus
# calls vector_store.delete_by_repository() before inserting anything
# from the fresh scan), not asserted by reading the code alone.


async def test_reindex_removes_a_previously_ingested_files_stale_chunks(db_session, monkeypatch):
    repository = await _make_repository(db_session)

    # Simulate a file that was really indexed by an earlier run (e.g. an
    # older code version, before this phase's filename exclusion existed)
    # and is now gone from the repository's current tree entirely -
    # deliberately not re-testing the exclusion filter itself here (the
    # tests above already cover that); this isolates "does a stale row
    # get cleaned up by a reindex" as its own question.
    stale_file = RepositoryFile(
        repository_id=repository.id,
        file_path="backend/.env",
        language=None,
        size_bytes=20,
        content_hash="stale",
    )
    db_session.add(stale_file)
    await db_session.flush()
    stale_chunk = CodeChunk(
        id=uuid.uuid4(),
        repository_id=repository.id,
        repository_file_id=stale_file.id,
        chunk_index=0,
        content="DB_PASSWORD=reallysecret",
        start_line=1,
        end_line=1,
        vector_id=str(uuid.uuid4()),
    )
    db_session.add(stale_chunk)
    await db_session.commit()

    tarball = _make_tarball({"backend/app/main.py": b"def create_app():\n    return {}\n"})

    async def _fake_download(owner, repo, ref):
        return tarball

    class _FakeEmbeddingProvider:
        async def embed_texts(self, texts):
            return [[0.0, 0.0] for _ in texts]

    delete_calls: list = []

    async def _fake_delete(repository_id):
        delete_calls.append(repository_id)

    async def _noop_upsert(points):
        return None

    monkeypatch.setattr("app.services.repository_ingestion._download_tarball", _fake_download)
    monkeypatch.setattr(
        "app.services.repository_ingestion.get_embedding_provider",
        lambda: _FakeEmbeddingProvider(),
    )
    monkeypatch.setattr("app.services.repository_ingestion.vector_store.upsert_chunks", _noop_upsert)
    monkeypatch.setattr(
        "app.services.repository_ingestion.vector_store.delete_by_repository", _fake_delete
    )
    monkeypatch.setattr("app.services.repository_ingestion.AsyncSessionLocal", lambda: db_session)

    await ingest_repository(repository.id)

    # Qdrant was told to drop every vector for this repository before the
    # fresh scan's vectors (none of which include the stale file) were
    # upserted - the stale point can't be left behind in Qdrant.
    assert delete_calls == [repository.id]

    # The stale repository_files row - and, via the real ON DELETE CASCADE
    # on code_chunks.repository_file_id, its code_chunks row - are both
    # gone, not just superseded.
    remaining_files = (
        await db_session.execute(
            select(RepositoryFile).where(RepositoryFile.repository_id == repository.id)
        )
    ).scalars().all()
    assert {f.file_path for f in remaining_files} == {"backend/app/main.py"}

    remaining_chunks = (
        await db_session.execute(select(CodeChunk).where(CodeChunk.repository_id == repository.id))
    ).scalars().all()
    assert not any(c.content == "DB_PASSWORD=reallysecret" for c in remaining_chunks)
