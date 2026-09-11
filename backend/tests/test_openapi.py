"""OpenAPI schema and API documentation regression tests (Day 49).

These lock in the generated schema's shape (title/version/tags/summaries/
documented error responses) and, separately, that none of the pure
documentation changes altered any actual response's JSON shape or status
code. Schema checks use `app.openapi()` directly (no running server
needed); a couple also hit GET /openapi.json through the shared `client`
fixture to confirm FastAPI actually serves it end to end.
"""

from __future__ import annotations

from app.main import app
from tests.conftest import register_and_login

# Endpoints that call no LLM/embedding provider at all - documenting a
# 502/503 for these would be exactly the "claims a status code the
# implementation does not return" mistake Day 49 asked to avoid.
_NO_AI_UPSTREAM_ENDPOINTS = [
    ("post", "/repositories/{repository_id}/security-scan"),
]


async def test_openapi_schema_is_served_and_valid(client):
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["openapi"]
    assert "paths" in schema and schema["paths"]
    assert "components" in schema


def test_api_title_and_version():
    schema = app.openapi()
    assert schema["info"]["title"] == "RepoMind AI API"
    assert schema["info"]["version"] == "1.0.0"
    assert schema["info"].get("description")


def test_expected_endpoint_paths_exist():
    schema = app.openapi()
    paths = schema["paths"]
    expected = [
        "/health",
        "/health/ready",
        "/auth/register",
        "/auth/login",
        "/auth/me",
        "/repositories",
        "/repositories/{repository_id}",
        "/repositories/{repository_id}/files",
        "/repositories/{repository_id}/chunks",
        "/repositories/{repository_id}/search",
        "/repositories/{repository_id}/debug",
        "/repositories/{repository_id}/architecture",
        "/repositories/{repository_id}/security-scan",
        "/repositories/{repository_id}/agent",
        "/repositories/{repository_id}/reindex",
        "/repositories/{repository_id}/files/{file_id}/explain",
        "/repositories/{repository_id}/files/{file_id}/review",
        "/repositories/{repository_id}/conversations",
        "/conversations/{conversation_id}/messages",
    ]
    for path in expected:
        assert path in paths, f"missing expected path: {path}"


def test_expected_tags_are_present():
    schema = app.openapi()
    tag_names = {t["name"] for t in schema.get("tags", [])}
    assert tag_names == {
        "Health",
        "Authentication",
        "Repositories",
        "Files",
        "Search",
        "Conversations",
        "AI Analysis",
        "Agent",
    }
    # Every declared tag has a description, not just a bare name.
    for tag in schema["tags"]:
        assert tag.get("description")


def test_every_operation_has_a_tag_and_summary():
    schema = app.openapi()
    for path, methods in schema["paths"].items():
        if path == "/":
            continue  # the bare root pointer isn't part of the tag taxonomy
        for method, operation in methods.items():
            assert operation.get("tags"), f"{method.upper()} {path} has no tag"
            assert operation.get("summary"), f"{method.upper()} {path} has no summary"


def test_documented_error_responses_match_implementation():
    schema = app.openapi()

    login = schema["paths"]["/auth/login"]["post"]
    assert set(login["responses"]) >= {"200", "401", "403", "422", "429"}

    get_repo = schema["paths"]["/repositories/{repository_id}"]["get"]
    assert set(get_repo["responses"]) >= {"200", "401", "404"}

    create_repo = schema["paths"]["/repositories"]["post"]
    assert set(create_repo["responses"]) >= {"201", "400", "401", "404", "429", "502"}

    # debug calls both embeddings and the LLM - both upstream failure
    # modes apply.
    debug = schema["paths"]["/repositories/{repository_id}/debug"]["post"]
    assert set(debug["responses"]) >= {"200", "401", "404", "429", "502", "503"}

    for method, path in _NO_AI_UPSTREAM_ENDPOINTS:
        operation = schema["paths"][path][method]
        assert "502" not in operation["responses"]
        assert "503" not in operation["responses"]


def test_error_responses_document_the_standard_detail_shape():
    schema = app.openapi()
    error_schema_ref = schema["components"]["schemas"]["ErrorDetail"]
    assert error_schema_ref["properties"]["detail"]["type"] == "string"

    not_found = schema["paths"]["/repositories/{repository_id}"]["get"]["responses"]["404"]
    assert "ErrorDetail" in str(not_found["content"]["application/json"]["schema"])


def test_security_scheme_metadata_is_present():
    schema = app.openapi()
    schemes = schema["components"]["securitySchemes"]
    assert "OAuth2PasswordBearer" in schemes

    protected = schema["paths"]["/auth/me"]["get"]
    assert protected["security"] == [{"OAuth2PasswordBearer": []}]

    # /health is public and must never require a token.
    health = schema["paths"]["/health"]["get"]
    assert not health.get("security")


async def test_health_and_readiness_still_function(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

    response = await client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


async def test_register_response_shape_is_unchanged(client):
    response = await client.post(
        "/auth/register",
        json={
            "email": "openapi-shape@example.com",
            "password": "TestPass123!",
            "full_name": "Shape Test",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert set(body.keys()) == {"id", "email", "full_name", "is_active", "created_at"}
    assert body["email"] == "openapi-shape@example.com"


async def test_list_repositories_response_shape_is_unchanged(client):
    headers = await register_and_login(client, "openapi-list-shape@example.com")
    response = await client.get("/repositories", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {
        "items",
        "page",
        "page_size",
        "total",
        "total_pages",
        "has_next",
        "has_previous",
    }


async def test_missing_token_still_returns_401_not_403(client):
    # Confirms the OAuth2PasswordBearer -> HTTPBearer swap that would have
    # broken this (403, not 401) was correctly avoided (Day 49).
    response = await client.get("/auth/me")
    assert response.status_code == 401
