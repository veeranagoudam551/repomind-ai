"""Unit/integration tests for app.core.rate_limit (Day 46).

The Redis client is faked with a tiny in-memory INCR/EXPIRE stand-in -
same "swap the external client, not the logic under test" approach as
httpx.MockTransport elsewhere in this suite - rather than needing a real
Redis server, consistent with this whole test suite's "no live network
access needed" property (see conftest.py's other stubbed externals,
including this file's own target: rate limiting is disabled by default
for every *other* test via conftest.py's autouse `_disable_rate_limiting`,
so these are the tests that explicitly turn it back on).
"""

from __future__ import annotations

import uuid

import pytest
import redis.exceptions

from app.core.config import settings
from app.core.rate_limit import (
    RateLimitExceeded,
    _check,
    per_ip_rate_limit,
    per_user_rate_limit,
)
from tests.conftest import register_and_login
from tests.factories import make_repo_info


@pytest.fixture(autouse=True)
def _reset_circuit_breaker(monkeypatch):
    # The breaker's "last failure" timestamp is deliberately module-level
    # state (see rate_limit.py) so it persists across requests in a real
    # process - but that means it would otherwise leak between tests too:
    # a broken-Redis test earlier in the run could make a later test's
    # _check() calls skip the fake client entirely via the still-open
    # breaker, never actually exercising what that test means to test.
    monkeypatch.setattr("app.core.rate_limit._last_failure_at", None)


class _FakeRedis:
    """In-memory stand-in exposing just the two calls _check makes."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.expirations: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, seconds: int) -> bool:
        self.expirations[key] = seconds
        return True


class _BrokenRedis:
    """Simulates a genuinely unreachable Redis, same failure mode Day 38
    fixed for Celery - a connection-level error, not a bad response."""

    async def incr(self, key: str) -> int:
        raise redis.exceptions.ConnectionError("could not connect to Redis")


def _enable(monkeypatch, limit: int = 3, window: int = 60) -> None:
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_ai_limit", limit)
    monkeypatch.setattr(settings, "rate_limit_ai_window_seconds", window)


async def test_requests_below_the_limit_succeed(monkeypatch):
    _enable(monkeypatch, limit=3)
    fake = _FakeRedis()
    monkeypatch.setattr("app.core.rate_limit._get_client", lambda: fake)

    for _ in range(3):
        await _check("ai", "user-1")  # no exception raised


async def test_requests_exceeding_the_limit_return_429(monkeypatch):
    _enable(monkeypatch, limit=3)
    fake = _FakeRedis()
    monkeypatch.setattr("app.core.rate_limit._get_client", lambda: fake)

    for _ in range(3):
        await _check("ai", "user-1")

    with pytest.raises(RateLimitExceeded) as exc_info:
        await _check("ai", "user-1")
    assert exc_info.value.status_code == 429
    assert "Rate limit exceeded" in exc_info.value.detail


async def test_limits_are_isolated_between_users(monkeypatch):
    _enable(monkeypatch, limit=1)
    fake = _FakeRedis()
    monkeypatch.setattr("app.core.rate_limit._get_client", lambda: fake)

    await _check("ai", "user-1")
    with pytest.raises(RateLimitExceeded):
        await _check("ai", "user-1")

    # A different identifier's own budget is untouched by user-1's usage -
    # this is what actually makes per-user limiting meaningful.
    await _check("ai", "user-2")


async def test_redis_failure_fails_open_rather_than_crashing(monkeypatch):
    _enable(monkeypatch, limit=1)
    monkeypatch.setattr("app.core.rate_limit._get_client", lambda: _BrokenRedis())

    # If this didn't fail open, the second/third calls would raise either
    # RateLimitExceeded (bogus - Redis never counted them) or the raw
    # ConnectionError itself. Neither happens: every call just returns.
    await _check("ai", "user-1")
    await _check("ai", "user-1")
    await _check("ai", "user-1")


async def test_circuit_breaker_skips_redis_after_a_recent_failure(monkeypatch):
    # Found live, not by inspection: without this breaker, every single
    # request independently pays the full connection-timeout cost when
    # Redis is down - measured at 3 real seconds added to a single
    # POST /auth/register against the real app (see rate_limit.py's
    # module docstring). This test proves the fix deterministically
    # (by call count, not by timing, so it can't be flaky) rather than
    # re-relying on the same live-timing measurement that caught the bug.
    _enable(monkeypatch, limit=1000)  # high enough to never trip on count
    calls: list[str] = []

    class _CountingBrokenRedis:
        async def incr(self, key: str) -> int:
            calls.append(key)
            raise redis.exceptions.ConnectionError("could not connect to Redis")

    monkeypatch.setattr("app.core.rate_limit._get_client", lambda: _CountingBrokenRedis())

    await _check("ai", "user-1")
    await _check("ai", "user-1")
    await _check("ai", "user-1")

    assert len(calls) == 1, (
        "only the first call should actually attempt Redis - the rest "
        "should be skipped by the breaker while it's still recent"
    )


async def test_disabled_rate_limiting_never_touches_redis(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", False)

    def _fail_if_called():
        raise AssertionError("should not touch Redis when rate limiting is disabled")

    monkeypatch.setattr("app.core.rate_limit._get_client", _fail_if_called)

    await _check("ai", "user-1")


async def test_per_user_rate_limit_keys_by_the_authenticated_users_id(monkeypatch):
    _enable(monkeypatch, limit=1)
    fake = _FakeRedis()
    monkeypatch.setattr("app.core.rate_limit._get_client", lambda: fake)

    class _StubUser:
        id = uuid.uuid4()

    dependency = per_user_rate_limit("ai")
    await dependency(current_user=_StubUser())
    with pytest.raises(RateLimitExceeded):
        # Same object's id -> same Redis key -> this call is the one over
        # the limit=1 budget.
        await dependency(current_user=_StubUser())


async def test_per_ip_rate_limit_keys_by_client_host(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_auth_register_limit", 1)
    monkeypatch.setattr(settings, "rate_limit_auth_register_window_seconds", 60)
    fake = _FakeRedis()
    monkeypatch.setattr("app.core.rate_limit._get_client", lambda: fake)

    class _StubClient:
        host = "203.0.113.5"

    class _StubRequest:
        client = _StubClient()

    dependency = per_ip_rate_limit("auth_register")
    await dependency(request=_StubRequest())
    with pytest.raises(RateLimitExceeded):
        await dependency(request=_StubRequest())


async def test_create_repository_returns_429_over_http_when_limit_exceeded(client, monkeypatch):
    # Integration-level: proves the dependency is actually wired into a
    # real route and produces a real HTTP 429, not just that the isolated
    # function raises - the unit tests above cover the logic, this one
    # covers the wiring.
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_ingestion_limit", 1)
    monkeypatch.setattr(settings, "rate_limit_ingestion_window_seconds", 3600)
    fake = _FakeRedis()
    monkeypatch.setattr("app.core.rate_limit._get_client", lambda: fake)

    async def _fetch(owner, repo):
        return make_repo_info(
            full_name=f"{owner}/{repo}", html_url=f"https://github.com/{owner}/{repo}"
        )

    monkeypatch.setattr("app.api.repositories.fetch_repository", _fetch)

    headers = await register_and_login(client, "ratelimit_http@example.com")

    first = await client.post(
        "/repositories", json={"github_url": "owner/repo-a"}, headers=headers
    )
    assert first.status_code == 201

    second = await client.post(
        "/repositories", json={"github_url": "owner/repo-b"}, headers=headers
    )
    assert second.status_code == 429
    assert "Rate limit exceeded" in second.json()["detail"]


async def test_second_user_is_unaffected_by_first_users_limit_over_http(client, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_ingestion_limit", 1)
    monkeypatch.setattr(settings, "rate_limit_ingestion_window_seconds", 3600)
    fake = _FakeRedis()
    monkeypatch.setattr("app.core.rate_limit._get_client", lambda: fake)

    async def _fetch(owner, repo):
        return make_repo_info(
            full_name=f"{owner}/{repo}", html_url=f"https://github.com/{owner}/{repo}"
        )

    monkeypatch.setattr("app.api.repositories.fetch_repository", _fetch)

    headers_a = await register_and_login(client, "ratelimit_usera@example.com")
    headers_b = await register_and_login(client, "ratelimit_userb@example.com")

    used_up = await client.post(
        "/repositories", json={"github_url": "owner/repo-a"}, headers=headers_a
    )
    assert used_up.status_code == 201

    blocked = await client.post(
        "/repositories", json={"github_url": "owner/repo-a2"}, headers=headers_a
    )
    assert blocked.status_code == 429

    still_allowed = await client.post(
        "/repositories", json={"github_url": "owner/repo-b"}, headers=headers_b
    )
    assert still_allowed.status_code == 201
