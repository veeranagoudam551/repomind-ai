"""Tests for Day 48's request ID middleware, structured logging, and the
catch-all unhandled-exception handler.
"""

from __future__ import annotations

import logging
import uuid

from app.main import app
from tests.conftest import register_and_login


async def test_request_id_is_generated_when_absent(client):
    response = await client.get("/health")
    request_id = response.headers.get("X-Request-ID")
    assert request_id
    uuid.UUID(request_id)  # a generated ID is a real UUID4


async def test_incoming_request_id_is_preserved_when_valid(client):
    response = await client.get("/health", headers={"X-Request-ID": "client-supplied-id-123"})
    assert response.headers.get("X-Request-ID") == "client-supplied-id-123"


async def test_invalid_incoming_request_id_is_replaced_not_reflected():
    # Not routed through the shared `client` fixture's AsyncClient, since
    # httpx itself would reject a raw CRLF in a header value before it
    # ever reached the app - this exercises app.core.request_id's own
    # validation directly against a value it could plausibly receive
    # (over-length, or containing characters outside the allowed set).
    from app.core.request_id import _resolve_request_id

    too_long = "a" * 500
    resolved = _resolve_request_id(too_long)
    assert resolved != too_long
    uuid.UUID(resolved)

    has_bad_chars = "id-with spaces-and-!@#"
    resolved = _resolve_request_id(has_bad_chars)
    assert resolved != has_bad_chars
    uuid.UUID(resolved)


async def test_every_response_includes_request_id_header(client):
    for path in ["/", "/health", "/health/ready"]:
        response = await client.get(path)
        assert response.headers.get("X-Request-ID")


async def test_request_log_includes_method_path_status_duration_and_request_id(client, caplog):
    caplog.set_level(logging.INFO, logger="app.core.request_id")

    response = await client.get("/health")
    request_id = response.headers["X-Request-ID"]

    matches = [
        r
        for r in caplog.records
        if r.name == "app.core.request_id" and getattr(r, "http_path", None) == "/health"
    ]
    assert matches, "expected a request-completed log record for GET /health"
    record = matches[-1]
    assert record.http_method == "GET"
    assert record.status_code == 200
    assert isinstance(record.duration_ms, float)
    assert record.duration_ms >= 0
    assert record.request_id == request_id


async def test_request_logging_does_not_include_query_params_or_body(client, caplog):
    caplog.set_level(logging.INFO, logger="app.core.request_id")

    await client.get("/health?secret_token=should-not-be-logged")

    for record in caplog.records:
        if record.name != "app.core.request_id":
            continue
        assert "secret_token" not in record.getMessage()
        assert "secret_token" not in str(record.__dict__.get("http_path", ""))


async def test_authorization_header_and_jwt_are_never_logged(client, caplog):
    caplog.set_level(logging.DEBUG)

    auth_headers = await register_and_login(client, "observability-secrets@example.com")
    token = auth_headers["Authorization"].split(" ", 1)[1]

    response = await client.get("/repositories", headers=auth_headers)
    assert response.status_code == 200

    for record in caplog.records:
        haystack = record.getMessage() + " ".join(str(v) for v in record.__dict__.values())
        assert token not in haystack
        assert auth_headers["Authorization"] not in haystack
        assert "Bearer" not in haystack


async def test_unhandled_exception_returns_generic_500_and_logs_stack_trace(client, caplog):
    caplog.set_level(logging.ERROR)

    @app.get("/__test-only/boom")
    def _boom():
        raise RuntimeError("boom - internal detail that must never reach the client")

    try:
        response = await client.get("/__test-only/boom")
    finally:
        app.router.routes[:] = [
            r for r in app.router.routes if getattr(r, "path", None) != "/__test-only/boom"
        ]

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    assert "boom - internal detail" not in response.text
    assert "Traceback" not in response.text
    assert response.headers.get("X-Request-ID")

    logged = [
        r
        for r in caplog.records
        if r.name == "app.core.request_id"
        and r.exc_info is not None
        and "boom" in str(r.exc_info[1])
    ]
    assert logged, "expected the unhandled exception to be logged with a stack trace"


async def test_health_endpoint_still_works_with_observability_enabled(client):
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "repomind-ai-backend"
