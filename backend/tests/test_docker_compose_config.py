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
    #
    # --concurrency=1 (deployment hardening): explicit rather than the
    # prefork pool's default of os.cpu_count() worker processes, each
    # capable of holding a full copy of the app (and, if
    # EMBEDDING_PROVIDER=local, the ONNX model) in memory - unpredictable
    # and potentially excessive on the target Oracle Ampere A1 VM.
    assert isinstance(command, list)
    assert command == [
        "celery", "-A", "app.core.celery_app", "worker", "--loglevel=info", "--concurrency=1",
    ]


def test_api_healthcheck_probe_has_explicit_timeout():
    compose = _load_compose()
    test = compose["services"]["api"]["healthcheck"]["test"]
    probe_code = test[-1]

    assert "timeout=3" in probe_code


def test_backend_env_passes_through_llm_provider_config():
    # Stabilization-phase fix: the api/celery-worker containers must see
    # LLM_PROVIDER/GROQ_API_KEY/GROQ_MODEL from the host's .env, not just
    # ANTHROPIC_API_KEY/OPENAI_API_KEY - without this, a `--profile full`
    # deployment configured for LLM_PROVIDER=groq on the host would
    # silently fall back to the unconfigured anthropic default inside the
    # containers, and every LLM-backed endpoint would fail with
    # LLMConfigError despite the host's own .env being correct.
    compose = _load_compose()
    backend_env = compose["services"]["api"]["environment"]
    assert backend_env["LLM_PROVIDER"] == "${LLM_PROVIDER:-anthropic}"
    assert backend_env["GROQ_API_KEY"] == "${GROQ_API_KEY}"
    assert backend_env["GROQ_MODEL"] == "${GROQ_MODEL:-openai/gpt-oss-120b}"
    # Shared anchor - celery-worker must get exactly the same values, not
    # a second, potentially-drifted copy.
    assert compose["services"]["celery-worker"]["environment"] == backend_env


# --- Deployment hardening (Caddy, host-port removal, Redis/Qdrant auth) ---


_NO_PUBLIC_PORTS = ("postgres", "redis", "qdrant", "api", "celery-worker", "frontend")


def test_only_caddy_publishes_host_ports():
    # The entire point of this hardening pass: every service except
    # caddy must have no `ports:` key at all (not even an empty list) -
    # Docker Compose's `ports:` publishes to the host's 0.0.0.0 by
    # default, and this project's own deployment-readiness audit found
    # every one of these five services host-published beforehand.
    compose = _load_compose()
    for name in _NO_PUBLIC_PORTS:
        assert "ports" not in compose["services"][name], f"{name} must not publish a host port"
    assert compose["services"]["caddy"]["ports"] == ["80:80", "443:443"]


def test_qdrant_grpc_port_is_never_published():
    # 6334 (gRPC) is never used by app/services/vector_store.py (a plain
    # REST client) - pure unnecessary attack surface if published, on
    # top of Qdrant already having no `ports:` at all per the test above.
    compose = _load_compose()
    ports = compose["services"]["qdrant"].get("ports", [])
    assert not any("6334" in str(p) for p in ports)


def test_redis_url_embeds_the_password_variable_with_safe_empty_default():
    # REDIS_PASSWORD empty (unset) must render as `redis://:@redis:...` -
    # verified live (not just by inspection) to be treated as "no AUTH
    # sent" by both redis-py (app.core.rate_limit) and Celery's kombu
    # transport, preserving today's unauthenticated local/CI Redis with
    # zero required setup.
    compose = _load_compose()
    backend_env = compose["services"]["api"]["environment"]
    assert backend_env["REDIS_URL"] == "redis://:${REDIS_PASSWORD:-}@redis:6379/0"


def test_qdrant_api_key_is_passed_through_to_both_backend_and_qdrant_itself():
    # Both sides of the same credential must reference the same
    # underlying variable, or a real deployment could set one without
    # the other and get a confusing, silent auth mismatch.
    compose = _load_compose()
    backend_env = compose["services"]["api"]["environment"]
    assert backend_env["QDRANT_API_KEY"] == "${QDRANT_API_KEY:-}"
    assert compose["services"]["qdrant"]["environment"]["QDRANT__SERVICE__API_KEY"] == "${QDRANT_API_KEY:-}"


def test_forwarded_allow_ips_default_matches_the_edge_network_subnet():
    # A stale mismatch here would silently narrow uvicorn's trust to a
    # subnet Caddy isn't even on, or widen it beyond what the network
    # topology actually justifies - this pins the two together so they
    # can't drift apart independently.
    compose = _load_compose()
    edge_subnet = compose["networks"]["edge"]["ipam"]["config"][0]["subnet"]
    backend_env = compose["services"]["api"]["environment"]
    assert backend_env["FORWARDED_ALLOW_IPS"] == f"${{FORWARDED_ALLOW_IPS:-{edge_subnet}}}"


def test_caddy_is_on_edge_network_with_frontend_and_api_and_data_stores_are_not():
    compose = _load_compose()
    services = compose["services"]
    assert set(services["caddy"]["networks"]) == {"edge"}
    assert set(services["frontend"]["networks"]) == {"edge"}
    assert set(services["api"]["networks"]) == {"edge", "internal"}
    # Postgres/Redis/Qdrant must never be reachable from `edge` - nothing
    # there (frontend, caddy) has any legitimate reason to reach them
    # directly.
    for name in ("postgres", "redis", "qdrant", "celery-worker"):
        assert "edge" not in services[name]["networks"]


def test_caddyfile_routes_to_frontend_and_api_with_no_hardcoded_domain():
    caddyfile = (Path(__file__).resolve().parents[2] / "docker" / "Caddyfile").read_text(encoding="utf-8")
    # Domain comes from the environment, never a literal hostname baked
    # into the file - the placeholder mechanism the deployment-hardening
    # task required.
    assert "{$PRODUCTION_DOMAIN}" in caddyfile
    assert "reverse_proxy frontend:3000" in caddyfile
    assert "reverse_proxy api:8000" in caddyfile
    # /api/* must strip the prefix before forwarding (handle_path, not
    # handle) - FastAPI's real routes (/auth/*, /repositories/*, /health)
    # don't start with /api, so a bare passthrough would 404 everything.
    assert "handle_path /api/*" in caddyfile
    # No literal "localhost"/"127.0.0.1" as the actual site address -
    # only ever inside a comment explaining the local-testing fallback.
    for line in caddyfile.splitlines():
        code = line.split("#", 1)[0]
        assert "localhost" not in code
        assert "127.0.0.1" not in code
