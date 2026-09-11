"""Structural checks for docker/docker-compose.yml (Day 47's review fixes).

Pure `yaml.safe_load` + dict assertions, no Docker/Compose CLI involved -
Docker Desktop isn't available on this dev machine, so this is the only
practical way to pin down the resolved YAML structure in CI/locally.
Guards against regressing the two bugs Day 47's review found:

  - The frontend build arg silently reusing NEXT_PUBLIC_API_BASE_URL
    (resolves to the frontend container itself under `--profile full`,
    not the api container) instead of the dedicated
    COMPOSE_FRONTEND_API_BASE_URL override.
  - celery-worker's `command` regressing back to a plain string (run via
    `sh -c`, losing PID-1/SIGTERM correctness) instead of exec-array form.
"""

from pathlib import Path

import yaml

_COMPOSE_PATH = Path(__file__).resolve().parents[2] / "docker" / "docker-compose.yml"


def _load_compose() -> dict:
    with open(_COMPOSE_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_infra_services_have_no_profile():
    # postgres/redis/qdrant must stay reachable via a plain
    # `docker compose up -d`, with no `--profile` flag needed - Day 40's
    # original behavior, unchanged by Day 47.
    compose = _load_compose()
    for name in ("postgres", "redis", "qdrant"):
        assert "profiles" not in compose["services"][name]


def test_full_profile_services_are_gated():
    compose = _load_compose()
    for name in ("api", "celery-worker", "frontend"):
        assert compose["services"][name]["profiles"] == ["full"]


def test_frontend_build_arg_uses_compose_internal_api_url_by_default():
    compose = _load_compose()
    build_args = compose["services"]["frontend"]["build"]["args"]
    resolved = build_args["NEXT_PUBLIC_API_BASE_URL"]

    # Must default to the Compose-internal service name, not localhost -
    # the frontend's Next.js server code runs inside its own container
    # under the `full` profile, so "localhost:8000" would resolve to
    # that container, not the separate api container.
    assert resolved == "${COMPOSE_FRONTEND_API_BASE_URL:-http://api:8000}"

    # The real Day 47 bug: reusing the native-dev variable directly.
    assert "NEXT_PUBLIC_API_BASE_URL" not in resolved


def test_celery_worker_command_is_exec_array_not_shell_string():
    compose = _load_compose()
    command = compose["services"]["celery-worker"]["command"]

    # A plain string is run via `sh -c "..."`, leaving celery as a child
    # of that shell rather than PID 1 - correct SIGTERM handling would
    # then depend on dash's own exec optimization rather than being
    # guaranteed.
    assert isinstance(command, list)
    assert command == ["celery", "-A", "app.core.celery_app", "worker", "--loglevel=info"]


def test_api_healthcheck_probe_has_explicit_timeout():
    compose = _load_compose()
    test = compose["services"]["api"]["healthcheck"]["test"]
    probe_code = test[-1]

    assert "timeout=3" in probe_code
