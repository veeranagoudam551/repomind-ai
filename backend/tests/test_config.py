"""Tests for Settings' production-safety guard (Day 47).

Every field is passed explicitly in each test rather than relying on
unset fields to fall through to the class default - this machine's real
.env (read via env_file="../.env") could otherwise override some of
them, and CI has no .env file at all, so relying on ambient state would
make these tests non-deterministic across environments.
"""

import pytest

from app.core.config import Settings

_PLACEHOLDER_JWT_SECRET = "changeme-generate-a-long-random-secret"
_REAL_JWT_SECRET = "a-genuinely-random-production-secret-value"
_PLACEHOLDER_DATABASE_URL = "postgresql+asyncpg://repomind:changeme@localhost:5432/repomind"
_REAL_DATABASE_URL = "postgresql+asyncpg://repomind:s3cur3-real-password@db.internal:5432/repomind"


def test_settings_rejects_default_jwt_secret_in_production():
    with pytest.raises(ValueError, match="JWT_SECRET_KEY"):
        Settings(
            environment="production",
            jwt_secret_key=_PLACEHOLDER_JWT_SECRET,
            database_url=_REAL_DATABASE_URL,
        )


def test_settings_rejects_empty_jwt_secret_in_production():
    # Distinct from the placeholder case: this is what Docker Compose's
    # ${VAR} substitution (no :-fallback) produces for an unset variable -
    # an empty string, not "unset falls through to the class default".
    with pytest.raises(ValueError, match="JWT_SECRET_KEY"):
        Settings(
            environment="production",
            jwt_secret_key="",
            database_url=_REAL_DATABASE_URL,
        )


def test_settings_rejects_default_database_url_in_production():
    with pytest.raises(ValueError, match="DATABASE_URL"):
        Settings(
            environment="production",
            jwt_secret_key=_REAL_JWT_SECRET,
            database_url=_PLACEHOLDER_DATABASE_URL,
        )


def test_settings_rejects_both_placeholders_at_once_with_one_error():
    with pytest.raises(ValueError, match="JWT_SECRET_KEY.*DATABASE_URL|DATABASE_URL.*JWT_SECRET_KEY"):
        Settings(
            environment="production",
            jwt_secret_key=_PLACEHOLDER_JWT_SECRET,
            database_url=_PLACEHOLDER_DATABASE_URL,
        )


def test_settings_accepts_real_values_in_production():
    settings = Settings(
        environment="production",
        jwt_secret_key=_REAL_JWT_SECRET,
        database_url=_REAL_DATABASE_URL,
    )
    assert settings.environment == "production"
    assert settings.jwt_secret_key == _REAL_JWT_SECRET


def test_settings_allows_placeholder_defaults_outside_production():
    # The whole point of these being defaults at all: a fresh checkout
    # boots with zero configuration per the README's `cp .env.example
    # .env` step. The guard only fires once ENVIRONMENT says this is real.
    settings = Settings(
        environment="development",
        jwt_secret_key=_PLACEHOLDER_JWT_SECRET,
        database_url=_PLACEHOLDER_DATABASE_URL,
    )
    assert settings.jwt_secret_key == _PLACEHOLDER_JWT_SECRET
    assert settings.database_url == _PLACEHOLDER_DATABASE_URL
