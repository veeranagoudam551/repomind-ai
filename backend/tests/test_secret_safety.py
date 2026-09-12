"""Secret-safety regression tests (Day 52).

Distinct from tests/test_observability.py's existing JWT/Authorization-
header check (Day 48, still the most direct one) and
tests/test_deployment_config.py's existing `.env.example`-only checks
(Day 50) - these cover the *other* secret-bearing settings this project
has (DATABASE_URL, OPENAI_API_KEY, ANTHROPIC_API_KEY, GITHUB_TOKEN)
across real request logging, and lock in two real findings from Day 52's
audit: a stale, inconsistent `backend/.env.example` that duplicated
(and diverged from) the canonical root contract, and that
docker-compose.yml's secret-bearing env entries are pure passthroughs
with no hardcoded fallback value.

Fake, obviously-not-real values only - never a real secret, per the
task's own instruction not to expose one during validation.
"""

from __future__ import annotations

import logging
from pathlib import Path

from tests.test_docker_compose_config import _load_compose

_REPO_ROOT = Path(__file__).resolve().parents[2]

_FAKE_SECRETS = {
    "jwt_secret_key": "test-jwt-secret-must-never-appear-in-a-log-line",
    "database_url": "postgresql+asyncpg://repomind:S3cretTestPassw0rd@db.internal:5432/repomind",
    "openai_api_key": "sk-fake-openai-secret-must-never-appear-in-a-log-line",
    "anthropic_api_key": "sk-ant-fake-secret-must-never-appear-in-a-log-line",
    "github_token": "ghp_fakeGithubTokenMustNeverAppearInALogLine",
}


async def test_no_secret_value_appears_in_logs_during_normal_requests(client, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    for field, value in _FAKE_SECRETS.items():
        monkeypatch.setattr(f"app.core.config.settings.{field}", value)

    # A mix of ordinary traffic - a public endpoint, a registration, and a
    # failed login - exercises Day 48's request-logging middleware, the
    # exception-logging path, and ordinary application logging alike,
    # none of which touch these five settings directly in this request
    # set, but that's exactly the point: they must never leak into logs
    # incidentally either.
    await client.get("/health")
    await client.post(
        "/auth/register",
        json={"email": "secret-safety@example.com", "password": "TestPass123!"},
    )
    await client.post(
        "/auth/login", json={"email": "no-such-user@example.com", "password": "wrong"}
    )

    haystacks = [
        record.getMessage() + " ".join(str(value) for value in record.__dict__.values())
        for record in caplog.records
    ]
    for field, value in _FAKE_SECRETS.items():
        assert not any(value in haystack for haystack in haystacks), (
            f"settings.{field}'s configured value leaked into a log record"
        )


def test_compose_secrets_are_pure_passthroughs_with_no_hardcoded_fallback():
    # docker-compose.yml's own comment already states this intent (no
    # local-dev-style default for a real secret, unlike POSTGRES_PASSWORD)
    # - this locks it in structurally rather than trusting the comment.
    compose = _load_compose()
    backend_env = compose["x-backend-env"]
    for var in ("JWT_SECRET_KEY", "GITHUB_TOKEN", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        value = backend_env[var]
        assert value == f"${{{var}}}", (
            f"{var} in docker-compose.yml's x-backend-env must be a pure "
            f"${{{var}}} passthrough with no hardcoded fallback, got {value!r}"
        )


def test_frontend_build_args_contain_only_the_public_api_url():
    # NEXT_PUBLIC_* values are inlined into the JavaScript shipped to the
    # browser - this build.args block must never grow a second entry that
    # could smuggle a backend secret into the frontend image.
    compose = _load_compose()
    build_args = compose["services"]["frontend"]["build"]["args"]
    assert set(build_args) == {"NEXT_PUBLIC_API_BASE_URL"}


def test_backend_directory_has_no_stale_duplicate_env_example():
    # Day 52 finding: backend/.env.example existed as a second, divergent
    # copy of the environment contract (a different Postgres user/db name
    # than the one docker-compose.yml's postgres service and the root
    # .env.example actually agree on) - and README's own setup command,
    # run with `cd backend` already in effect, would silently copy *this*
    # file instead of the canonical root one. Removed; this guards against
    # it quietly coming back.
    assert not (_REPO_ROOT / "backend" / ".env.example").exists()
