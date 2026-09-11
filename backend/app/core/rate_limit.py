"""Redis-backed rate limiting (architecture.md Day 46).

A fixed-window counter per (scope, identifier): INCR a Redis key, EXPIRE
it the first time it's touched within the window, and reject once the
count exceeds the configured limit. Deliberately hand-rolled against the
`redis` package's own async client (already a dependency since Day 34,
used there as the Celery broker) rather than a rate-limiting library -
the same "thin wrapper over a plain client" style already used for
Qdrant/GitHub/OpenAI/Anthropic in this codebase (vector_store.py,
github.py, embeddings.py, llm.py), and there's exactly one primitive
needed here (INCR+EXPIRE), not a framework's worth of surface area.

Fails open, not closed: if Redis itself is unreachable or slow, the
request is allowed through (with a logged warning) rather than a broker
outage taking down every rate-limited endpoint - matching how Day 38
already treats "Redis is down" as a normal, handled condition rather
than something that should crash or hang a request. Short, explicit
socket timeouts on the client are the same defense Day 38 added to the
Celery broker connection, for the same reason: an unreachable (as
opposed to actively refused) host can otherwise hang a connect() call
for the OS's own TCP timeout, not anything Python-level.

A short-lived circuit breaker sits in front of that timeout: found live,
not by inspection - the very first end-to-end verification of this
feature (this project's own e2e suite, Redis genuinely unreachable, the
same disclosed environment gap Days 38-45 already live with) turned up
a real regression the unit tests alone couldn't have caught, since they
fake the client instead of actually connecting. Timing a single real
`POST /auth/register` showed *3 seconds* added by the socket timeout
alone - correct in the "doesn't crash" sense, but every request paying
that cost independently is its own kind of outage, and cascaded into
seemingly unrelated e2e failures (Playwright's 5s default assertion
timeout on the post-register redirect). The breaker remembers a recent
failure for `_FAILURE_COOLDOWN_SECONDS` and skips straight to failing
open during that window - the connection attempt is retried on the very
next call afterward, so recovery is automatic - so only the *first*
request after Redis actually goes down pays the timeout; every one
after that (the common case for either a real outage or, as here, a
test/dev environment with no Redis at all) returns immediately.

Per-user, not per-IP, for every authenticated scope - the whole point is
isolating users from each other's usage, and per-IP would also make
automated tests (many distinct users, one machine) collide with each
other. The `auth_*` scopes are the one exception: register/login happen
before a user exists, so there's nothing but the client's IP to key on.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Optional

import redis.asyncio as redis
from fastapi import Depends, HTTPException, Request, status

from app.api.deps import get_current_user
from app.core.config import settings
from app.models.user import User

logger = logging.getLogger(__name__)

_SOCKET_TIMEOUT_SECONDS = 1.0
_FAILURE_COOLDOWN_SECONDS = 10.0

_redis_client: Optional[redis.Redis] = None
_last_failure_at: Optional[float] = None


def _get_client() -> redis.Redis:
    # Lazy + cached: a module-level singleton, same shape as
    # app.core.celery_app's module-level Celery app. Tests monkeypatch
    # this function itself (not the client it returns) to inject a fake.
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(
            settings.redis_url,
            socket_connect_timeout=_SOCKET_TIMEOUT_SECONDS,
            socket_timeout=_SOCKET_TIMEOUT_SECONDS,
        )
    return _redis_client


_SCOPE_SETTINGS = {
    "ingestion": ("rate_limit_ingestion_limit", "rate_limit_ingestion_window_seconds"),
    "ai": ("rate_limit_ai_limit", "rate_limit_ai_window_seconds"),
    "agent": ("rate_limit_agent_limit", "rate_limit_agent_window_seconds"),
    "auth_register": ("rate_limit_auth_register_limit", "rate_limit_auth_register_window_seconds"),
    "auth_login": ("rate_limit_auth_login_limit", "rate_limit_auth_login_window_seconds"),
}


def _scope_config(scope: str) -> tuple[int, int]:
    # Read from `settings` fresh on every call rather than capturing the
    # values as closure arguments when the dependency is constructed at
    # route-decoration time - so a test's `monkeypatch.setattr(settings,
    # "rate_limit_ai_limit", 2)` actually takes effect on the next request,
    # and so an operator's env var change takes effect without a restart's
    # worth of extra indirection to reason about.
    limit_field, window_field = _SCOPE_SETTINGS[scope]
    return getattr(settings, limit_field), getattr(settings, window_field)


class RateLimitExceeded(HTTPException):
    def __init__(self, limit: int, window_seconds: int):
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Rate limit exceeded: {limit} requests per "
                f"{window_seconds} seconds for this action. Please try "
                "again later."
            ),
        )


def _breaker_is_open() -> bool:
    return (
        _last_failure_at is not None
        and (time.monotonic() - _last_failure_at) < _FAILURE_COOLDOWN_SECONDS
    )


async def _check(scope: str, identifier: str) -> None:
    global _last_failure_at

    if not settings.rate_limit_enabled:
        return

    if _breaker_is_open():
        # Still within the cooldown from a recent failure - don't pay
        # another connection timeout just to rediscover what we already
        # know. Recovers on its own: the next call after the cooldown
        # elapses retries for real, and success clears the breaker below.
        return

    limit, window_seconds = _scope_config(scope)
    key = f"ratelimit:{scope}:{identifier}"

    try:
        client = _get_client()
        count = await client.incr(key)
        if count == 1:
            await client.expire(key, window_seconds)
    except redis.RedisError as exc:
        logger.warning("Rate limit check failed for %r (failing open): %s", key, exc)
        _last_failure_at = time.monotonic()
        return

    _last_failure_at = None

    if count > limit:
        raise RateLimitExceeded(limit, window_seconds)


def per_user_rate_limit(scope: str) -> Callable:
    """A FastAPI dependency that rate-limits by the authenticated user's id.

    Depends on `get_current_user` itself rather than requiring every route
    to inject and pass along a `User`, and reuses whatever that dependency
    already resolved this request (FastAPI caches a dependency's result
    per callable, per request) rather than looking the user up twice.
    """

    async def _dependency(current_user: User = Depends(get_current_user)) -> None:
        await _check(scope, str(current_user.id))

    return _dependency


def per_ip_rate_limit(scope: str) -> Callable:
    """A FastAPI dependency that rate-limits by client IP, for endpoints
    with no authenticated user yet (register/login)."""

    async def _dependency(request: Request) -> None:
        client_host = request.client.host if request.client else "unknown"
        await _check(scope, client_host)

    return _dependency
