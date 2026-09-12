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
_BACKEND_DIR = _REPO_ROOT / "backend"

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


# --- Day 51 review: fastembed isolated to its own requirements file ---


def _requirement_lines(path: Path) -> list:
    # Non-comment, non-blank lines only - a comment is allowed to mention
    # a package name while explaining why it's deliberately absent.
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def test_default_requirements_do_not_include_fastembed():
    # The whole point of the split - a default `pip install -r
    # requirements.txt` (and the "production" Docker target, which COPYs
    # only this file) must never pull in fastembed/onnxruntime/onnx/
    # numpy/tokenizers (~150MB) for an EMBEDDING_PROVIDER=openai deploy.
    lines = _requirement_lines(_BACKEND_DIR / "requirements.txt")
    assert not any("fastembed" in line.lower() for line in lines)


def test_local_embedding_requirements_extend_the_default_ones():
    content = (_BACKEND_DIR / "requirements-local-embedding.txt").read_text(encoding="utf-8")
    assert "-r requirements.txt" in content
    assert "fastembed" in content.lower()


def test_dev_requirements_include_local_embedding_support():
    # The test suite exercises the local provider for real
    # (tests/test_embedding_providers.py) - `pip install -r
    # requirements-dev.txt` must be enough to run it, no separate step.
    content = (_BACKEND_DIR / "requirements-dev.txt").read_text(encoding="utf-8")
    assert "requirements-local-embedding.txt" in content


def test_dockerfile_has_a_lightweight_default_and_an_explicit_local_embedding_target():
    content = (_BACKEND_DIR / "Dockerfile").read_text(encoding="utf-8")
    stage_order = [line for line in content.splitlines() if line.startswith("FROM")]
    assert any("AS production" in line for line in stage_order)
    assert any("AS with-local-embedding" in line for line in stage_order)
    # "production" must be the *last* FROM - Docker's own default build
    # target when `--target`/`target:` isn't given at all.
    assert stage_order[-1].endswith("AS production")


def test_compose_backend_services_default_to_the_production_docker_target():
    compose = _load_compose()
    for name in ("api", "celery-worker"):
        target = compose["services"][name]["build"]["target"]
        assert target == "${BACKEND_DOCKER_TARGET:-production}"
