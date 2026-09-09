"""Shared pytest fixtures.

Tests run against a real Postgres database (`<DATABASE_URL's db>_test`,
created automatically if missing) rather than mocks, since the models use
Postgres-specific column types. Each test gets a freshly recreated schema.
Network calls to GitHub and the background ingestion pipeline (already
verified manually in Days 7-8) are stubbed out so the suite is fast and
deterministic.
"""

from __future__ import annotations

from typing import AsyncGenerator

import asyncpg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.database import get_db
from app.main import app
from app.models.base import Base

_base_url = make_url(settings.database_url)
_test_db_name = f"{_base_url.database}_test"
TEST_DATABASE_URL = _base_url.set(database=_test_db_name).render_as_string(hide_password=False)


async def _ensure_test_database() -> None:
    maintenance_url = _base_url.set(database="postgres")
    conn = await asyncpg.connect(
        user=maintenance_url.username,
        password=maintenance_url.password,
        host=maintenance_url.host,
        port=maintenance_url.port or 5432,
    )
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", _test_db_name)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{_test_db_name}"')
    finally:
        await conn.close()


@pytest_asyncio.fixture
async def test_engine() -> AsyncGenerator:
    # Function-scoped (not session-scoped): pytest-asyncio gives each test
    # function its own event loop by default, and asyncpg connections are
    # bound to the loop they were created on, so a shared engine would break
    # across tests. Recreating the schema per test is cheap enough here.
    await _ensure_test_database()
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture(autouse=True)
def _stub_ingestion(monkeypatch):
    async def _noop(repository_id):
        return None

    monkeypatch.setattr("app.api.repositories.ingest_repository", _noop)


@pytest.fixture(autouse=True)
def _stub_vector_store(monkeypatch):
    # No live Qdrant server in the test environment, same reasoning as
    # stubbing GitHub/ingestion above. tests/test_vector_store.py covers
    # the Qdrant HTTP calls themselves via a mocked transport.
    async def _noop_delete(repository_id):
        return None

    monkeypatch.setattr("app.services.vector_store.delete_by_repository", _noop_delete)


@pytest_asyncio.fixture
async def db_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(test_engine) -> AsyncGenerator[AsyncClient, None]:
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(get_db, None)


async def register_and_login(client: AsyncClient, email: str, password: str = "TestPass123!") -> dict:
    await client.post(
        "/auth/register",
        json={"email": email, "password": password, "full_name": "Test User"},
    )
    response = await client.post("/auth/login", json={"email": email, "password": password})
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
