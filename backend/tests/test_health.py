"""Tests for /health (liveness) and /health/ready (Day 47's readiness
probe). Neither needs auth - both are meant for a load balancer/
orchestrator with no credentials.
"""

import asyncio


async def test_health_check_returns_ok(client):
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "repomind-ai-backend"


async def test_readiness_check_returns_ready_when_database_is_reachable(client):
    response = await client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


async def test_readiness_check_returns_503_when_database_is_unreachable(client, monkeypatch):
    # Simulates the "unreachable, not just erroring" case the timeout
    # exists for - any failure at all should map to the same graceful
    # 503, never an unhandled exception or a hang.
    async def _raise_timeout(coro, *args, **kwargs):
        coro.close()  # avoid a "coroutine was never awaited" warning
        raise asyncio.TimeoutError("simulated unreachable database")

    monkeypatch.setattr("app.api.health.asyncio.wait_for", _raise_timeout)

    response = await client.get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not ready"
    # No connection string, credentials, or other infra detail leaked.
    assert "postgresql" not in str(body).lower()
    assert "changeme" not in str(body).lower()
