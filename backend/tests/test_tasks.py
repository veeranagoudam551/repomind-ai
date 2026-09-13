"""Regression test for a real bug found while manually verifying Day 34
against a genuine Celery worker (see app/tasks.py's docstring for the
full explanation): app.tasks.ingest_repository_task wraps ingest_repository
in asyncio.run(), and a worker process handles many tasks over its
lifetime, each getting a brand new event loop from that call. Without
disposing app.core.database.engine's connection pool at the end of every
task, the *second* task in a worker process hands back a connection
opened on the *first* task's now-closed loop and crashes.

The rest of the suite never catches this because test_repository_ingestion.py
monkeypatches AsyncSessionLocal to the test's own already-open db_session
(a single connection, no pool, no cross-loop reuse possible) rather than
exercising a real connection-pooled engine the way the worker actually
does. This test uses a real `create_async_engine` instead, and drives
`ingest_repository_task` (the synchronous Celery task, not the underlying
async function) through two separate asyncio.run() cycles in a worker
thread - since asyncio.run() cannot be called from a coroutine that's
already running on this test's own event loop, but a plain thread has no
running loop of its own, exactly like a separate worker process wouldn't.
"""

from __future__ import annotations

import asyncio
import io
import tarfile
import uuid

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.repository import Repository, RepositoryStatus
from app.models.user import User
from tests.conftest import TEST_DATABASE_URL


def _make_tarball(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        root_info = tarfile.TarInfo(name="repo-main")
        root_info.type = tarfile.DIRTYPE
        # Needs the execute bit or the extracted directory is non-traversable
        # on Linux (TarInfo() defaults to mode 0o644 regardless of type) -
        # see test_repository_ingestion.py's _make_tarball for the full
        # explanation; this is an independent copy of the same helper with
        # the same bug, caught by the same Day 44 Linux CI run.
        root_info.mode = 0o755
        tar.addfile(root_info)
        for rel_path, content in files.items():
            info = tarfile.TarInfo(name=f"repo-main/{rel_path}")
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def test_make_tarball_root_directory_is_traversable():
    # Same regression test as test_repository_ingestion.py's own - this
    # file has an independent copy of _make_tarball, so it needs its own
    # independent guard against the same regression.
    data = _make_tarball({"README.md": b"hello\n"})
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        root = tar.getmember("repo-main")
    assert root.isdir()
    assert root.mode & 0o100, f"root directory mode {oct(root.mode)} is missing the owner execute bit"


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


async def test_worker_survives_two_ingestion_tasks_in_a_row(db_session, monkeypatch):
    repo_one = await _make_repository(db_session)
    repo_two = await _make_repository(db_session)

    tarball = _make_tarball({"README.md": b"hello\n"})

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

    # A real, connection-pooled engine bound to the test database - not
    # db_session's single already-open connection, since the whole point
    # is to exercise a pool that outlives an individual asyncio.run() loop.
    task_engine = create_async_engine(TEST_DATABASE_URL)
    task_session_factory = async_sessionmaker(task_engine, expire_on_commit=False)
    monkeypatch.setattr(
        "app.services.repository_ingestion.AsyncSessionLocal", task_session_factory
    )
    monkeypatch.setattr("app.tasks.engine", task_engine)

    from app.tasks import ingest_repository_task

    loop = asyncio.get_running_loop()
    # Each call is its own asyncio.run() cycle in a worker thread, exactly
    # like a worker process handling two tasks back to back - a thread has
    # no event loop of its own running, so this doesn't hit "asyncio.run()
    # cannot be called from a running event loop" the way calling it
    # directly from this async test function would.
    await loop.run_in_executor(None, ingest_repository_task, str(repo_one.id))
    await loop.run_in_executor(None, ingest_repository_task, str(repo_two.id))

    # Read back through task_session_factory, not db_session - db_session
    # still holds repo_one/repo_two in its identity map from creating them
    # (expire_on_commit=False), so it would silently return the original
    # PENDING-status Python objects rather than re-querying what the tasks
    # actually wrote through their own, separate connection.
    async with task_session_factory() as verify_session:
        updated_one = await verify_session.get(Repository, repo_one.id)
        updated_two = await verify_session.get(Repository, repo_two.id)
        assert updated_one.status == RepositoryStatus.COMPLETED
        assert updated_two.status == RepositoryStatus.COMPLETED

    await task_engine.dispose()


class _NoopEngine:
    """Stands in for app.core.database.engine in tests that exercise
    _mark_failed_after_timeout directly through db_session (a single
    already-open connection, not a real pool) - there's nothing meaningful
    to dispose there, and disposing the real module-level engine as a side
    effect of a unit test would be reaching into global state this test
    doesn't own."""

    async def dispose(self) -> None:
        return None


async def test_mark_failed_after_timeout_sets_failed_status_and_message(
    db_session, monkeypatch
):
    # Day 59: the recovery path app.tasks.ingest_repository_task falls back
    # to when a Celery soft time limit's SoftTimeLimitExceeded unwinds
    # outside ingest_repository's own try/except (see that function's own
    # comment on why that can happen) - tested directly here, independent
    # of the signal/asyncio.run() plumbing itself.
    repository = await _make_repository(db_session)
    repository.status = RepositoryStatus.PROCESSING
    await db_session.commit()

    monkeypatch.setattr("app.tasks.AsyncSessionLocal", lambda: db_session)
    monkeypatch.setattr("app.tasks.engine", _NoopEngine())

    from app.tasks import _TIMEOUT_ERROR_MESSAGE, _mark_failed_after_timeout

    await _mark_failed_after_timeout(repository.id)

    updated = await db_session.get(Repository, repository.id)
    assert updated.status == RepositoryStatus.FAILED
    assert updated.error_message == _TIMEOUT_ERROR_MESSAGE


async def test_mark_failed_after_timeout_does_not_overwrite_a_completed_repository(
    db_session, monkeypatch
):
    # Guards the race where ingestion actually finished successfully at
    # essentially the same moment the time limit fired - the recovery path
    # must never clobber a real COMPLETED result with a spurious FAILED one.
    repository = await _make_repository(db_session)
    repository.status = RepositoryStatus.COMPLETED
    await db_session.commit()

    monkeypatch.setattr("app.tasks.AsyncSessionLocal", lambda: db_session)
    monkeypatch.setattr("app.tasks.engine", _NoopEngine())

    from app.tasks import _mark_failed_after_timeout

    await _mark_failed_after_timeout(repository.id)

    updated = await db_session.get(Repository, repository.id)
    assert updated.status == RepositoryStatus.COMPLETED


async def test_ingest_repository_task_recovers_repository_on_soft_time_limit(
    db_session, monkeypatch
):
    # End-to-end version of the two tests above: exercises the actual
    # ingest_repository_task wiring (catch SoftTimeLimitExceeded, run the
    # recovery coroutine, re-raise so Celery still records the task as
    # failed) rather than calling the recovery function directly. Same
    # thread-executor technique as test_worker_survives_two_ingestion_tasks_in_a_row
    # above, for the same reason - a real, connection-pooled engine, driven
    # from a thread with no event loop of its own.
    repository = await _make_repository(db_session)

    async def _raise_soft_time_limit(repository_id):
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr("app.tasks._run_ingestion", _raise_soft_time_limit)

    task_engine = create_async_engine(TEST_DATABASE_URL)
    task_session_factory = async_sessionmaker(task_engine, expire_on_commit=False)
    monkeypatch.setattr("app.tasks.AsyncSessionLocal", task_session_factory)
    monkeypatch.setattr("app.tasks.engine", task_engine)

    from app.tasks import ingest_repository_task

    loop = asyncio.get_running_loop()

    with pytest.raises(SoftTimeLimitExceeded):
        await loop.run_in_executor(None, ingest_repository_task, str(repository.id))

    async with task_session_factory() as verify_session:
        updated = await verify_session.get(Repository, repository.id)
        assert updated.status == RepositoryStatus.FAILED
        assert updated.error_message == (
            "Ingestion exceeded the maximum allowed time and was stopped."
        )

    await task_engine.dispose()
