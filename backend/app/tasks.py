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
"""

from __future__ import annotations

import asyncio
import uuid

from app.core.celery_app import celery_app
from app.core.database import engine
from app.services.repository_ingestion import ingest_repository


async def _run_ingestion(repository_id: uuid.UUID) -> None:
    try:
        await ingest_repository(repository_id)
    finally:
        await engine.dispose()


@celery_app.task(name="ingest_repository")
def ingest_repository_task(repository_id: str) -> None:
    asyncio.run(_run_ingestion(uuid.UUID(repository_id)))
