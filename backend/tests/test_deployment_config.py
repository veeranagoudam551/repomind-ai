"""Deployment/environment-contract regression tests (Day 50).

Distinct from tests/test_docker_compose_config.py (Day 47's blocker fix,
already covering the frontend build-arg/profile/celery-command/timeout
invariants) - these lock in the Day 50 audit's own findings instead:
that Qdrant's healthcheck-less service is never falsely depended on as
"healthy", that the frontend's build-time-only API URL never gains a
runtime twin, that every setting the app can actually read is
documented in .env.example, and that .env.example itself never carries
a real-looking secret.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from app.core.config import Settings

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE_PATH = _REPO_ROOT / "docker" / "docker-compose.yml"
_ENV_EXAMPLE_PATH = _REPO_ROOT / ".env.example"

_ENV_LINE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")


def _load_compose() -> dict:
    with open(_COMPOSE_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_env_example() -> dict:
    values = {}
    for line in _ENV_EXAMPLE_PATH.read_text(encoding="utf-8").splitlines():
        match = _ENV_LINE.match(line.strip())
        if match:
            values[match.group(1)] = match.group(2)
    return values


def test_qdrant_has_no_healthcheck_and_is_never_depended_on_as_healthy():
    compose = _load_compose()
    services = compose["services"]

    # Qdrant's official image has no guaranteed shell tools to probe
    # with (docker-compose.yml's own comment) - it must stay
    # healthcheck-less rather than someone adding an unreliable one.
    assert "healthcheck" not in services["qdrant"]

    # Anything that depends on qdrant must use "service_started", never
    # "service_healthy" - there is no healthcheck to report healthy in
    # the first place, so Compose would either refuse to start (no
    # healthcheck defined) or the dependency would be meaningless.
    for name in ("api", "celery-worker"):
        qdrant_dependency = services[name]["depends_on"]["qdrant"]
        assert qdrant_dependency["condition"] == "service_started"


def test_frontend_service_has_no_runtime_environment_block():
    # NEXT_PUBLIC_API_BASE_URL is inlined by Next.js at build time
    # (frontend/next.config.ts's `output: "standalone"` +
    # frontend/Dockerfile's ARG/ENV in the builder stage only) - it must
    # never also appear as a runtime `environment:` entry on this
    # service, which would wrongly imply changing it after the image is
    # built has any effect.
    compose = _load_compose()
    frontend = compose["services"]["frontend"]
    assert "environment" not in frontend


def test_env_example_documents_every_setting_the_app_can_read():
    documented = set(_load_env_example())
    for field_name in Settings.model_fields:
        env_var = field_name.upper()
        assert env_var in documented, f"{env_var} (Settings.{field_name}) is not documented in .env.example"


_PLACEHOLDER_SECRET_VALUES = {
    "",
    "changeme",
    "changeme-generate-a-long-random-secret",
}


def test_env_example_contains_no_real_looking_secrets():
    values = _load_env_example()
    secret_vars = [
        "JWT_SECRET_KEY",
        "POSTGRES_PASSWORD",
        "GITHUB_TOKEN",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
    ]
    for var in secret_vars:
        assert var in values, f"{var} missing from .env.example entirely"
        assert values[var] in _PLACEHOLDER_SECRET_VALUES, (
            f"{var}={values[var]!r} in .env.example doesn't look like a placeholder - "
            "committed secrets must never happen"
        )
