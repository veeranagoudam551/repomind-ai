"""Celery tasks (architecture.md Phase 5 - background workers).

One task today: repository ingestion, moved off FastAPI's in-process
`BackgroundTasks` (Days 7-33) onto a real queue. `ingest_repository`
itself is untouched - it already opens its own `AsyncSessionLocal`
rather than depending on a request-scoped session, so wrapping it in
`asyncio.run()` inside a synchronous Celery task is the only change
needed to run it from a worker process instead of the API process.

A worker process handles many tasks over its lifetime, each getting its
own fresh event loop from `asyncio.run()` - but `app.core.database.engine`
is a module-level connection pool shared across every call, and asyncpg
connections are bound to the event loop they were opened on. Without
disposing the pool before its loop closes, the *second* task in a worker
hands back a connection from the *first* task's now-dead loop and
crashes (`AttributeError: 'NoneType' object has no attribute 'send'`,
found by actually running two real tasks through a real worker rather
than the single-task pytest coverage, which never exercises a second
task in the same process). Disposing at the end of every task, inside
the same still-open loop, guarantees the next task's `asyncio.run()`
always starts from an empty pool.

Day 59: bounded execution time + a timeout recovery path, on top of the
above. `time_limit`/`soft_time_limit` below are only enforced by
Celery's prefork pool (the default on Linux, what `docker-compose.yml`
and CI actually run) - the `--pool=solo` workaround README documents for
native Windows dev (no `os.fork()`) does not support time limits at all,
a known Celery limitation, not a bug here. 600s/660s were chosen relative
to this pipeline's own per-call timeouts (a 60s GitHub tarball download,
30s-per-batch OpenAI embedding calls, one 30s Qdrant upsert) - generous
enough that no legitimately-sized repository under `max_repo_size_mb`
should approach it under normal network conditions, while still bounding
how long a single pathological/hung task can occupy a worker slot.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from celery.exceptions import SoftTimeLimitExceeded

from app.core.celery_app import celery_app
from app.core.database import AsyncSessionLocal, engine
from app.models.repository import Repository, RepositoryStatus
from app.services.repository_ingestion import ingest_repository

logger = logging.getLogger(__name__)

_TIMEOUT_ERROR_MESSAGE = "Ingestion exceeded the maximum allowed time and was stopped."

# Soft limit fires first (raises SoftTimeLimitExceeded - a subclass of
# Exception, so ingest_repository's own `except Exception` already
# transitions the repository to FAILED if this propagates into that
# function's own frame), with a 60s gap before the hard limit's SIGKILL as
# a backstop for the recovery path below to actually finish running.
_INGESTION_SOFT_TIME_LIMIT_SECONDS = 600
_INGESTION_HARD_TIME_LIMIT_SECONDS = 660


async def _run_ingestion(repository_id: uuid.UUID) -> None:
    try:
        await ingest_repository(repository_id)
    finally:
        await engine.dispose()


async def _mark_failed_after_timeout(repository_id: uuid.UUID) -> None:
    """Best-effort recovery for the case where SoftTimeLimitExceeded's
    signal-based interrupt unwinds out through asyncio's own event-loop
    internals rather than through ingest_repository's own try/except (a
    real, documented rough edge of combining Celery's prefork time limits
    with asyncio.run() - the signal can be delivered while the process is
    blocked inside the event loop's own select()/epoll_wait() call, not
    necessarily while ingest_repository's own Python frame is executing).
    Runs as a completely separate, minimal session/event loop rather than
    depending on whatever state the interrupted one was left in - the same
    "dispose before the next task starts" invariant _run_ingestion already
    relies on applies here too."""
    try:
        async with AsyncSessionLocal() as db:
            repository = await db.get(Repository, repository_id)
            if repository is not None and repository.status != RepositoryStatus.COMPLETED:
                repository.status = RepositoryStatus.FAILED
                repository.error_message = _TIMEOUT_ERROR_MESSAGE
                await db.commit()
    finally:
        await engine.dispose()


@celery_app.task(
    name="ingest_repository",
    time_limit=_INGESTION_HARD_TIME_LIMIT_SECONDS,
    soft_time_limit=_INGESTION_SOFT_TIME_LIMIT_SECONDS,
)
def ingest_repository_task(repository_id: str) -> None:
    try:
        asyncio.run(_run_ingestion(uuid.UUID(repository_id)))
    except SoftTimeLimitExceeded:
        # Belt-and-suspenders: if ingest_repository's own exception handling
        # somehow didn't already catch this (see _mark_failed_after_timeout's
        # docstring), make sure the repository still ends up FAILED rather
        # than stuck in CLONING/PROCESSING forever with no error message and
        # no way to reindex it (reindex_repository refuses to act on a
        # repository that still looks "in progress").
        logger.error("ingest_repository_task: %s exceeded its time limit", repository_id)
        asyncio.run(_mark_failed_after_timeout(uuid.UUID(repository_id)))
        raise
