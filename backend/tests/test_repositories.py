import json
import uuid

from sqlalchemy import select

from app.models.code_chunk import CodeChunk
from app.models.repository import Repository, RepositoryStatus
from app.models.repository_file import RepositoryFile
from app.services.embeddings import EmbeddingConfigError
from app.services.github import GitHubAPIError, GitHubRepoNotFound
from app.services.llm import LLMConfigError
from tests.conftest import register_and_login
from tests.factories import make_repo_info


def _mock_fetch(monkeypatch, result=None, exc=None):
    async def _fetch(owner, repo):
        if exc is not None:
            raise exc
        return result

    monkeypatch.setattr("app.api.repositories.fetch_repository", _fetch)


async def test_create_repository_requires_auth(client):
    response = await client.post("/repositories", json={"github_url": "octocat/Hello-World"})
    assert response.status_code == 401


async def test_create_repository_success(client, monkeypatch):
    headers = await register_and_login(client, "owner1@example.com")
    _mock_fetch(monkeypatch, result=make_repo_info())

    response = await client.post(
        "/repositories", json={"github_url": "octocat/Hello-World"}, headers=headers
    )
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "octocat/Hello-World"
    assert body["status"] == "pending"
    assert body["file_count"] == 0


async def test_create_repository_invalid_url(client):
    headers = await register_and_login(client, "owner2@example.com")
    response = await client.post(
        "/repositories", json={"github_url": "not a url"}, headers=headers
    )
    assert response.status_code == 400


async def test_create_repository_not_found_on_github(client, monkeypatch):
    headers = await register_and_login(client, "owner3@example.com")
    _mock_fetch(monkeypatch, exc=GitHubRepoNotFound("nope"))

    response = await client.post(
        "/repositories", json={"github_url": "owner/does-not-exist"}, headers=headers
    )
    assert response.status_code == 404


async def test_create_repository_github_api_error(client, monkeypatch):
    headers = await register_and_login(client, "owner4@example.com")
    _mock_fetch(monkeypatch, exc=GitHubAPIError("rate limited"))

    response = await client.post(
        "/repositories", json={"github_url": "owner/repo"}, headers=headers
    )
    assert response.status_code == 502


async def test_create_repository_rejects_private(client, monkeypatch):
    headers = await register_and_login(client, "owner5@example.com")
    _mock_fetch(monkeypatch, result=make_repo_info(private=True))

    response = await client.post(
        "/repositories", json={"github_url": "owner/repo"}, headers=headers
    )
    assert response.status_code == 400


async def test_create_repository_rejects_oversized(client, monkeypatch):
    headers = await register_and_login(client, "owner6@example.com")
    _mock_fetch(monkeypatch, result=make_repo_info(size_kb=1024 * 1024))

    response = await client.post(
        "/repositories", json={"github_url": "owner/repo"}, headers=headers
    )
    assert response.status_code == 400


async def test_create_repository_rejects_duplicate(client, monkeypatch):
    headers = await register_and_login(client, "owner7@example.com")
    _mock_fetch(monkeypatch, result=make_repo_info())

    first = await client.post(
        "/repositories", json={"github_url": "octocat/Hello-World"}, headers=headers
    )
    second = await client.post(
        "/repositories", json={"github_url": "octocat/Hello-World"}, headers=headers
    )
    assert first.status_code == 201
    assert second.status_code == 400


async def test_create_repository_fails_fast_when_queueing_fails(client, monkeypatch):
    # Day 38: a broker that's unreachable/erroring shouldn't hang or 500 the
    # request - the repository should land in a terminal `failed` state with
    # a clear message instead, same as any other ingestion failure.
    headers = await register_and_login(client, "queuefailure@example.com")
    _mock_fetch(monkeypatch, result=make_repo_info())

    def _boom(repository_id):
        raise ConnectionError("could not connect to broker")

    monkeypatch.setattr("app.api.repositories.ingest_repository_task.delay", _boom)

    response = await client.post(
        "/repositories", json={"github_url": "octocat/Hello-World"}, headers=headers
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "failed"
    assert "background worker" in body["error_message"]


async def test_list_repositories_only_shows_own(client, monkeypatch):
    headers_a = await register_and_login(client, "usera@example.com")
    headers_b = await register_and_login(client, "userb@example.com")
    _mock_fetch(monkeypatch, result=make_repo_info())

    await client.post("/repositories", json={"github_url": "octocat/Hello-World"}, headers=headers_a)

    response_a = await client.get("/repositories", headers=headers_a)
    response_b = await client.get("/repositories", headers=headers_b)

    assert len(response_a.json()) == 1
    assert response_b.json() == []


async def test_get_repository_not_found_for_other_user(client, monkeypatch):
    headers_a = await register_and_login(client, "userc@example.com")
    headers_b = await register_and_login(client, "userd@example.com")
    _mock_fetch(monkeypatch, result=make_repo_info())

    created = await client.post(
        "/repositories", json={"github_url": "octocat/Hello-World"}, headers=headers_a
    )
    repo_id = created.json()["id"]

    own_view = await client.get(f"/repositories/{repo_id}", headers=headers_a)
    other_view = await client.get(f"/repositories/{repo_id}", headers=headers_b)

    assert own_view.status_code == 200
    assert other_view.status_code == 404


async def test_get_repository_unknown_id_returns_404(client):
    headers = await register_and_login(client, "usere@example.com")
    response = await client.get(
        "/repositories/00000000-0000-0000-0000-000000000000", headers=headers
    )
    assert response.status_code == 404


async def test_files_and_chunks_empty_before_ingestion_completes(client, monkeypatch):
    headers = await register_and_login(client, "userf@example.com")
    _mock_fetch(monkeypatch, result=make_repo_info())

    created = await client.post(
        "/repositories", json={"github_url": "octocat/Hello-World"}, headers=headers
    )
    repo_id = created.json()["id"]

    files_response = await client.get(f"/repositories/{repo_id}/files", headers=headers)
    chunks_response = await client.get(f"/repositories/{repo_id}/chunks", headers=headers)

    assert files_response.status_code == 200
    assert files_response.json() == []
    assert chunks_response.status_code == 200
    assert chunks_response.json() == []


async def test_delete_repository_removes_it_and_cascades(client, db_session, monkeypatch):
    headers = await register_and_login(client, "deleter@example.com")
    _mock_fetch(monkeypatch, result=make_repo_info())

    created = await client.post(
        "/repositories", json={"github_url": "octocat/Hello-World"}, headers=headers
    )
    repo_id = uuid.UUID(created.json()["id"])

    repo_file = RepositoryFile(repository_id=repo_id, file_path="README.md", size_bytes=10)
    db_session.add(repo_file)
    await db_session.commit()
    await db_session.refresh(repo_file)

    chunk = CodeChunk(
        repository_id=repo_id, repository_file_id=repo_file.id, chunk_index=0, content="hi"
    )
    db_session.add(chunk)
    await db_session.commit()

    response = await client.delete(f"/repositories/{repo_id}", headers=headers)
    assert response.status_code == 204

    get_response = await client.get(f"/repositories/{repo_id}", headers=headers)
    assert get_response.status_code == 404

    remaining_files = (
        await db_session.execute(select(RepositoryFile).where(RepositoryFile.repository_id == repo_id))
    ).scalars().all()
    remaining_chunks = (
        await db_session.execute(select(CodeChunk).where(CodeChunk.repository_id == repo_id))
    ).scalars().all()
    assert remaining_files == []
    assert remaining_chunks == []


async def test_delete_repository_cleans_up_vector_store(client, monkeypatch):
    headers = await register_and_login(client, "vectorcleanup@example.com")
    _mock_fetch(monkeypatch, result=make_repo_info())

    created = await client.post(
        "/repositories", json={"github_url": "octocat/Hello-World"}, headers=headers
    )
    repo_id = uuid.UUID(created.json()["id"])

    calls = []

    async def _spy(repository_id):
        calls.append(repository_id)

    monkeypatch.setattr("app.services.vector_store.delete_by_repository", _spy)

    response = await client.delete(f"/repositories/{repo_id}", headers=headers)
    assert response.status_code == 204
    assert calls == [repo_id]


async def test_delete_repository_vector_store_error_returns_502(client, monkeypatch):
    # Day 39: found live in e2e with a real, unreachable Qdrant - this
    # endpoint was the one Qdrant-touching route in the app not catching
    # VectorStoreError, so a Qdrant failure surfaced as an unhandled 500
    # instead of the same graceful 502 every other such endpoint returns.
    headers = await register_and_login(client, "vectorerror@example.com")
    _mock_fetch(monkeypatch, result=make_repo_info())

    created = await client.post(
        "/repositories", json={"github_url": "octocat/Hello-World"}, headers=headers
    )
    repo_id = created.json()["id"]

    from app.services.vector_store import VectorStoreError

    async def _boom(repository_id):
        raise VectorStoreError("Qdrant unreachable")

    monkeypatch.setattr("app.api.repositories.vector_store.delete_by_repository", _boom)

    response = await client.delete(f"/repositories/{repo_id}", headers=headers)
    assert response.status_code == 502

    # The repository row must survive a failed Qdrant delete - retryable,
    # not orphaned - same guarantee the existing code comment promises.
    get_response = await client.get(f"/repositories/{repo_id}", headers=headers)
    assert get_response.status_code == 200


async def test_delete_repository_not_found_for_other_user(client, monkeypatch):
    headers_a = await register_and_login(client, "deleterowner@example.com")
    headers_b = await register_and_login(client, "deleterintruder@example.com")
    _mock_fetch(monkeypatch, result=make_repo_info())

    created = await client.post(
        "/repositories", json={"github_url": "octocat/Hello-World"}, headers=headers_a
    )
    repo_id = created.json()["id"]

    response = await client.delete(f"/repositories/{repo_id}", headers=headers_b)
    assert response.status_code == 404


async def test_delete_repository_unknown_id_returns_404(client):
    headers = await register_and_login(client, "deleterghost@example.com")
    response = await client.delete(
        "/repositories/00000000-0000-0000-0000-000000000000", headers=headers
    )
    assert response.status_code == 404


async def test_reindex_rejects_while_ingestion_in_progress(client, monkeypatch):
    headers = await register_and_login(client, "reindexer1@example.com")
    _mock_fetch(monkeypatch, result=make_repo_info())

    created = await client.post(
        "/repositories", json={"github_url": "octocat/Hello-World"}, headers=headers
    )
    repo_id = created.json()["id"]
    assert created.json()["status"] == "pending"

    response = await client.post(f"/repositories/{repo_id}/reindex", headers=headers)
    assert response.status_code == 409


async def test_reindex_triggers_background_ingestion(client, db_session, monkeypatch):
    headers = await register_and_login(client, "reindexer2@example.com")
    _mock_fetch(monkeypatch, result=make_repo_info())

    created = await client.post(
        "/repositories", json={"github_url": "octocat/Hello-World"}, headers=headers
    )
    repo_id = uuid.UUID(created.json()["id"])

    repo = await db_session.get(Repository, repo_id)
    repo.status = RepositoryStatus.FAILED
    repo.error_message = "boom"
    await db_session.commit()

    calls = []

    def _spy(repository_id):
        calls.append(repository_id)

    monkeypatch.setattr("app.api.repositories.ingest_repository_task.delay", _spy)

    response = await client.post(f"/repositories/{repo_id}/reindex", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending"
    assert body["error_message"] is None
    assert calls == [str(repo_id)]


async def test_reindex_fails_fast_when_queueing_fails(client, db_session, monkeypatch):
    headers = await register_and_login(client, "reindexqueuefailure@example.com")
    _mock_fetch(monkeypatch, result=make_repo_info())

    created = await client.post(
        "/repositories", json={"github_url": "octocat/Hello-World"}, headers=headers
    )
    repo_id = uuid.UUID(created.json()["id"])

    repo = await db_session.get(Repository, repo_id)
    repo.status = RepositoryStatus.FAILED
    repo.error_message = "boom"
    await db_session.commit()

    def _boom(repository_id):
        raise ConnectionError("could not connect to broker")

    monkeypatch.setattr("app.api.repositories.ingest_repository_task.delay", _boom)

    response = await client.post(f"/repositories/{repo_id}/reindex", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert "background worker" in body["error_message"]


async def test_reindex_not_found_for_other_user(client, monkeypatch):
    headers_a = await register_and_login(client, "reindexowner@example.com")
    headers_b = await register_and_login(client, "reindexintruder@example.com")
    _mock_fetch(monkeypatch, result=make_repo_info())

    created = await client.post(
        "/repositories", json={"github_url": "octocat/Hello-World"}, headers=headers_a
    )
    repo_id = created.json()["id"]

    response = await client.post(f"/repositories/{repo_id}/reindex", headers=headers_b)
    assert response.status_code == 404


async def test_reindex_unknown_id_returns_404(client):
    headers = await register_and_login(client, "reindexghost@example.com")
    response = await client.post(
        "/repositories/00000000-0000-0000-0000-000000000000/reindex", headers=headers
    )
    assert response.status_code == 404


async def _make_searchable_repository(client, db_session, monkeypatch, headers) -> uuid.UUID:
    _mock_fetch(monkeypatch, result=make_repo_info())
    created = await client.post(
        "/repositories", json={"github_url": "octocat/Hello-World"}, headers=headers
    )
    repo_id = uuid.UUID(created.json()["id"])

    repo_file = RepositoryFile(repository_id=repo_id, file_path="app/main.py", size_bytes=10)
    db_session.add(repo_file)
    await db_session.commit()
    await db_session.refresh(repo_file)

    chunk = CodeChunk(
        repository_id=repo_id,
        repository_file_id=repo_file.id,
        chunk_index=0,
        content="def create_app(): ...",
        start_line=1,
        end_line=1,
        vector_id="whatever",
    )
    db_session.add(chunk)
    await db_session.commit()
    await db_session.refresh(chunk)
    return repo_id, chunk


async def test_search_requires_auth(client):
    response = await client.post(
        "/repositories/00000000-0000-0000-0000-000000000000/search", json={"query": "hi"}
    )
    assert response.status_code == 401


async def test_search_returns_ranked_results(client, db_session, monkeypatch):
    headers = await register_and_login(client, "searcher1@example.com")
    repo_id, chunk = await _make_searchable_repository(client, db_session, monkeypatch, headers)

    async def _fake_embed(text):
        return [0.1, 0.2]

    async def _fake_search(vector, repository_id, limit=10):
        assert vector == [0.1, 0.2]
        assert str(repository_id) == str(repo_id)
        return [{"id": str(chunk.id), "score": 0.87, "payload": {"code_chunk_id": str(chunk.id)}}]

    monkeypatch.setattr("app.api.repositories.generate_embedding", _fake_embed)
    monkeypatch.setattr("app.api.repositories.vector_store.search", _fake_search)

    response = await client.post(
        f"/repositories/{repo_id}/search", json={"query": "how is the app created"}, headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["code_chunk_id"] == str(chunk.id)
    assert body[0]["file_path"] == "app/main.py"
    assert body[0]["content"] == "def create_app(): ..."
    assert body[0]["score"] == 0.87


async def test_search_returns_empty_when_no_hits(client, db_session, monkeypatch):
    headers = await register_and_login(client, "searcher2@example.com")
    repo_id, _chunk = await _make_searchable_repository(client, db_session, monkeypatch, headers)

    async def _fake_embed(text):
        return [0.1, 0.2]

    async def _fake_search(vector, repository_id, limit=10):
        return []

    monkeypatch.setattr("app.api.repositories.generate_embedding", _fake_embed)
    monkeypatch.setattr("app.api.repositories.vector_store.search", _fake_search)

    response = await client.post(
        f"/repositories/{repo_id}/search", json={"query": "nothing matches"}, headers=headers
    )
    assert response.status_code == 200
    assert response.json() == []


async def test_search_not_found_for_other_user(client, db_session, monkeypatch):
    headers_a = await register_and_login(client, "searchowner@example.com")
    headers_b = await register_and_login(client, "searchintruder@example.com")
    repo_id, _chunk = await _make_searchable_repository(client, db_session, monkeypatch, headers_a)

    response = await client.post(
        f"/repositories/{repo_id}/search", json={"query": "hi"}, headers=headers_b
    )
    assert response.status_code == 404


async def test_search_maps_embedding_config_error_to_503(client, db_session, monkeypatch):
    headers = await register_and_login(client, "searcher3@example.com")
    repo_id, _chunk = await _make_searchable_repository(client, db_session, monkeypatch, headers)

    async def _fake_embed(text):
        raise EmbeddingConfigError("OPENAI_API_KEY is not configured")

    monkeypatch.setattr("app.api.repositories.generate_embedding", _fake_embed)

    response = await client.post(
        f"/repositories/{repo_id}/search", json={"query": "hi"}, headers=headers
    )
    assert response.status_code == 503


async def test_search_rejects_empty_query(client, db_session, monkeypatch):
    headers = await register_and_login(client, "searcher4@example.com")
    repo_id, _chunk = await _make_searchable_repository(client, db_session, monkeypatch, headers)

    response = await client.post(
        f"/repositories/{repo_id}/search", json={"query": ""}, headers=headers
    )
    assert response.status_code == 422


async def test_explain_file_success(client, db_session, monkeypatch):
    headers = await register_and_login(client, "explainer1@example.com")
    repo_id, chunk = await _make_searchable_repository(client, db_session, monkeypatch, headers)

    async def _fake_generate_response(system_prompt, user_message, max_tokens=1024):
        assert "app/main.py" in user_message
        assert "def create_app(): ..." in user_message
        return "This file defines the FastAPI application factory."

    monkeypatch.setattr("app.api.repositories.generate_response", _fake_generate_response)

    response = await client.post(
        f"/repositories/{repo_id}/files/{chunk.repository_file_id}/explain", headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["file_path"] == "app/main.py"
    assert body["explanation"] == "This file defines the FastAPI application factory."


async def test_explain_file_not_found(client, db_session, monkeypatch):
    headers = await register_and_login(client, "explainer2@example.com")
    repo_id, _chunk = await _make_searchable_repository(client, db_session, monkeypatch, headers)

    response = await client.post(
        f"/repositories/{repo_id}/files/00000000-0000-0000-0000-000000000000/explain",
        headers=headers,
    )
    assert response.status_code == 404


async def test_explain_file_not_found_for_other_user(client, db_session, monkeypatch):
    headers_a = await register_and_login(client, "explainowner@example.com")
    headers_b = await register_and_login(client, "explainintruder@example.com")
    repo_id, chunk = await _make_searchable_repository(client, db_session, monkeypatch, headers_a)

    response = await client.post(
        f"/repositories/{repo_id}/files/{chunk.repository_file_id}/explain", headers=headers_b
    )
    assert response.status_code == 404


async def test_explain_file_rejects_when_no_chunks(client, db_session, monkeypatch):
    headers = await register_and_login(client, "explainer3@example.com")
    _mock_fetch(monkeypatch, result=make_repo_info())
    created = await client.post(
        "/repositories", json={"github_url": "octocat/Hello-World"}, headers=headers
    )
    repo_id = uuid.UUID(created.json()["id"])

    repo_file = RepositoryFile(repository_id=repo_id, file_path="image.png", size_bytes=10)
    db_session.add(repo_file)
    await db_session.commit()
    await db_session.refresh(repo_file)

    response = await client.post(
        f"/repositories/{repo_id}/files/{repo_file.id}/explain", headers=headers
    )
    assert response.status_code == 400


async def test_explain_file_maps_llm_config_error_to_503(client, db_session, monkeypatch):
    headers = await register_and_login(client, "explainer4@example.com")
    repo_id, chunk = await _make_searchable_repository(client, db_session, monkeypatch, headers)

    async def _fake_generate_response(system_prompt, user_message, max_tokens=1024):
        raise LLMConfigError("ANTHROPIC_API_KEY is not configured")

    monkeypatch.setattr("app.api.repositories.generate_response", _fake_generate_response)

    response = await client.post(
        f"/repositories/{repo_id}/files/{chunk.repository_file_id}/explain", headers=headers
    )
    assert response.status_code == 503


async def test_review_file_success(client, db_session, monkeypatch):
    headers = await register_and_login(client, "reviewer1@example.com")
    repo_id, chunk = await _make_searchable_repository(client, db_session, monkeypatch, headers)

    async def _fake_generate_response(system_prompt, user_message, max_tokens=1024):
        assert "app/main.py" in user_message
        assert "def create_app(): ..." in user_message
        return "No issues found; the file is small and clean."

    monkeypatch.setattr("app.api.repositories.generate_response", _fake_generate_response)

    response = await client.post(
        f"/repositories/{repo_id}/files/{chunk.repository_file_id}/review", headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["file_path"] == "app/main.py"
    assert body["review"] == "No issues found; the file is small and clean."


async def test_review_file_not_found(client, db_session, monkeypatch):
    headers = await register_and_login(client, "reviewer2@example.com")
    repo_id, _chunk = await _make_searchable_repository(client, db_session, monkeypatch, headers)

    response = await client.post(
        f"/repositories/{repo_id}/files/00000000-0000-0000-0000-000000000000/review",
        headers=headers,
    )
    assert response.status_code == 404


async def test_review_file_not_found_for_other_user(client, db_session, monkeypatch):
    headers_a = await register_and_login(client, "reviewowner@example.com")
    headers_b = await register_and_login(client, "reviewintruder@example.com")
    repo_id, chunk = await _make_searchable_repository(client, db_session, monkeypatch, headers_a)

    response = await client.post(
        f"/repositories/{repo_id}/files/{chunk.repository_file_id}/review", headers=headers_b
    )
    assert response.status_code == 404


async def test_review_file_rejects_when_no_chunks(client, db_session, monkeypatch):
    headers = await register_and_login(client, "reviewer3@example.com")
    _mock_fetch(monkeypatch, result=make_repo_info())
    created = await client.post(
        "/repositories", json={"github_url": "octocat/Hello-World"}, headers=headers
    )
    repo_id = uuid.UUID(created.json()["id"])

    repo_file = RepositoryFile(repository_id=repo_id, file_path="image.png", size_bytes=10)
    db_session.add(repo_file)
    await db_session.commit()
    await db_session.refresh(repo_file)

    response = await client.post(
        f"/repositories/{repo_id}/files/{repo_file.id}/review", headers=headers
    )
    assert response.status_code == 400


async def test_review_file_maps_llm_config_error_to_503(client, db_session, monkeypatch):
    headers = await register_and_login(client, "reviewer4@example.com")
    repo_id, chunk = await _make_searchable_repository(client, db_session, monkeypatch, headers)

    async def _fake_generate_response(system_prompt, user_message, max_tokens=1024):
        raise LLMConfigError("ANTHROPIC_API_KEY is not configured")

    monkeypatch.setattr("app.api.repositories.generate_response", _fake_generate_response)

    response = await client.post(
        f"/repositories/{repo_id}/files/{chunk.repository_file_id}/review", headers=headers
    )
    assert response.status_code == 503


async def test_debug_requires_auth(client):
    response = await client.post(
        "/repositories/00000000-0000-0000-0000-000000000000/debug",
        json={"description": "it crashes"},
    )
    assert response.status_code == 401


async def test_debug_returns_diagnosis_with_sources(client, db_session, monkeypatch):
    headers = await register_and_login(client, "debugger1@example.com")
    repo_id, chunk = await _make_searchable_repository(client, db_session, monkeypatch, headers)

    async def _fake_embed(text):
        return [0.1, 0.2]

    async def _fake_search(vector, repository_id, limit=10):
        assert vector == [0.1, 0.2]
        assert str(repository_id) == str(repo_id)
        return [{"id": str(chunk.id), "score": 0.9, "payload": {"code_chunk_id": str(chunk.id)}}]

    async def _fake_generate_response(system_prompt, user_message, max_tokens=1024):
        assert "app/main.py" in user_message
        assert "NoneType has no attribute" in user_message
        return "The crash happens because create_app() returns None."

    monkeypatch.setattr("app.api.repositories.generate_embedding", _fake_embed)
    monkeypatch.setattr("app.api.repositories.vector_store.search", _fake_search)
    monkeypatch.setattr("app.api.repositories.generate_response", _fake_generate_response)

    response = await client.post(
        f"/repositories/{repo_id}/debug",
        json={"description": "NoneType has no attribute 'foo' on startup"},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["diagnosis"] == "The crash happens because create_app() returns None."
    assert len(body["sources"]) == 1
    assert body["sources"][0]["code_chunk_id"] == str(chunk.id)
    assert body["sources"][0]["file_path"] == "app/main.py"


async def test_debug_returns_canned_reply_when_no_hits(client, db_session, monkeypatch):
    headers = await register_and_login(client, "debugger2@example.com")
    repo_id, _chunk = await _make_searchable_repository(client, db_session, monkeypatch, headers)

    async def _fake_embed(text):
        return [0.1, 0.2]

    async def _fake_search(vector, repository_id, limit=10):
        return []

    async def _fail_if_called(*args, **kwargs):
        raise AssertionError("LLM should not be called when there is no retrieved context")

    monkeypatch.setattr("app.api.repositories.generate_embedding", _fake_embed)
    monkeypatch.setattr("app.api.repositories.vector_store.search", _fake_search)
    monkeypatch.setattr("app.api.repositories.generate_response", _fail_if_called)

    response = await client.post(
        f"/repositories/{repo_id}/debug", json={"description": "anything"}, headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["sources"] == []
    assert "couldn't find any indexed code" in body["diagnosis"]


async def test_debug_not_found_for_other_user(client, db_session, monkeypatch):
    headers_a = await register_and_login(client, "debugowner@example.com")
    headers_b = await register_and_login(client, "debugintruder@example.com")
    repo_id, _chunk = await _make_searchable_repository(client, db_session, monkeypatch, headers_a)

    response = await client.post(
        f"/repositories/{repo_id}/debug", json={"description": "hi"}, headers=headers_b
    )
    assert response.status_code == 404


async def test_debug_maps_embedding_config_error_to_503(client, db_session, monkeypatch):
    headers = await register_and_login(client, "debugger3@example.com")
    repo_id, _chunk = await _make_searchable_repository(client, db_session, monkeypatch, headers)

    async def _fake_embed(text):
        raise EmbeddingConfigError("OPENAI_API_KEY is not configured")

    monkeypatch.setattr("app.api.repositories.generate_embedding", _fake_embed)

    response = await client.post(
        f"/repositories/{repo_id}/debug", json={"description": "hi"}, headers=headers
    )
    assert response.status_code == 503


async def test_debug_maps_llm_config_error_to_503(client, db_session, monkeypatch):
    headers = await register_and_login(client, "debugger4@example.com")
    repo_id, chunk = await _make_searchable_repository(client, db_session, monkeypatch, headers)

    async def _fake_embed(text):
        return [0.1, 0.2]

    async def _fake_search(vector, repository_id, limit=10):
        return [{"id": str(chunk.id), "score": 0.9, "payload": {"code_chunk_id": str(chunk.id)}}]

    async def _fake_generate_response(system_prompt, user_message, max_tokens=1024):
        raise LLMConfigError("ANTHROPIC_API_KEY is not configured")

    monkeypatch.setattr("app.api.repositories.generate_embedding", _fake_embed)
    monkeypatch.setattr("app.api.repositories.vector_store.search", _fake_search)
    monkeypatch.setattr("app.api.repositories.generate_response", _fake_generate_response)

    response = await client.post(
        f"/repositories/{repo_id}/debug", json={"description": "hi"}, headers=headers
    )
    assert response.status_code == 503


async def test_debug_rejects_empty_description(client, db_session, monkeypatch):
    headers = await register_and_login(client, "debugger5@example.com")
    repo_id, _chunk = await _make_searchable_repository(client, db_session, monkeypatch, headers)

    response = await client.post(
        f"/repositories/{repo_id}/debug", json={"description": ""}, headers=headers
    )
    assert response.status_code == 422


async def test_architecture_requires_auth(client):
    response = await client.post(
        "/repositories/00000000-0000-0000-0000-000000000000/architecture"
    )
    assert response.status_code == 401


async def test_architecture_returns_analysis_with_readme(client, db_session, monkeypatch):
    headers = await register_and_login(client, "architect1@example.com")
    repo_id, _chunk = await _make_searchable_repository(client, db_session, monkeypatch, headers)

    readme_file = RepositoryFile(repository_id=repo_id, file_path="README.md", size_bytes=20)
    db_session.add(readme_file)
    await db_session.commit()
    await db_session.refresh(readme_file)
    readme_chunk = CodeChunk(
        repository_id=repo_id,
        repository_file_id=readme_file.id,
        chunk_index=0,
        content="# Demo\nA demo Flask app.",
        start_line=1,
        end_line=2,
    )
    db_session.add(readme_chunk)
    await db_session.commit()

    async def _fake_generate_response(system_prompt, user_message, max_tokens=1024):
        assert "app/main.py" in user_message
        assert "README.md" in user_message
        assert "A demo Flask app." in user_message
        return "This is a small Flask application with a single entry point."

    monkeypatch.setattr("app.api.repositories.generate_response", _fake_generate_response)

    response = await client.post(f"/repositories/{repo_id}/architecture", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["analysis"] == "This is a small Flask application with a single entry point."
    assert body["file_count"] == 2
    assert body["readme_path"] == "README.md"


async def test_architecture_returns_analysis_without_readme(client, db_session, monkeypatch):
    headers = await register_and_login(client, "architect2@example.com")
    repo_id, _chunk = await _make_searchable_repository(client, db_session, monkeypatch, headers)

    async def _fake_generate_response(system_prompt, user_message, max_tokens=1024):
        assert "README" not in user_message
        return "This appears to be a small Python project."

    monkeypatch.setattr("app.api.repositories.generate_response", _fake_generate_response)

    response = await client.post(f"/repositories/{repo_id}/architecture", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["file_count"] == 1
    assert body["readme_path"] is None


async def test_architecture_not_found_for_other_user(client, db_session, monkeypatch):
    headers_a = await register_and_login(client, "architectowner@example.com")
    headers_b = await register_and_login(client, "architectintruder@example.com")
    repo_id, _chunk = await _make_searchable_repository(client, db_session, monkeypatch, headers_a)

    response = await client.post(f"/repositories/{repo_id}/architecture", headers=headers_b)
    assert response.status_code == 404


async def test_architecture_rejects_when_no_files(client, monkeypatch):
    headers = await register_and_login(client, "architect3@example.com")
    _mock_fetch(monkeypatch, result=make_repo_info())
    created = await client.post(
        "/repositories", json={"github_url": "octocat/Hello-World"}, headers=headers
    )
    repo_id = created.json()["id"]

    response = await client.post(f"/repositories/{repo_id}/architecture", headers=headers)
    assert response.status_code == 400


async def test_architecture_maps_llm_config_error_to_503(client, db_session, monkeypatch):
    headers = await register_and_login(client, "architect4@example.com")
    repo_id, _chunk = await _make_searchable_repository(client, db_session, monkeypatch, headers)

    async def _fake_generate_response(system_prompt, user_message, max_tokens=1024):
        raise LLMConfigError("ANTHROPIC_API_KEY is not configured")

    monkeypatch.setattr("app.api.repositories.generate_response", _fake_generate_response)

    response = await client.post(f"/repositories/{repo_id}/architecture", headers=headers)
    assert response.status_code == 503


async def test_security_scan_requires_auth(client):
    response = await client.post(
        "/repositories/00000000-0000-0000-0000-000000000000/security-scan"
    )
    assert response.status_code == 401


async def test_security_scan_detects_findings(client, db_session, monkeypatch):
    headers = await register_and_login(client, "scanner1@example.com")
    repo_id, _chunk = await _make_searchable_repository(client, db_session, monkeypatch, headers)

    unsafe_file = RepositoryFile(repository_id=repo_id, file_path="app/config.py", size_bytes=40)
    db_session.add(unsafe_file)
    await db_session.commit()
    await db_session.refresh(unsafe_file)
    unsafe_chunk = CodeChunk(
        repository_id=repo_id,
        repository_file_id=unsafe_file.id,
        chunk_index=0,
        content="password = 'supersecretpass123'\nresult = eval(user_input)\n",
        start_line=1,
        end_line=2,
    )
    db_session.add(unsafe_chunk)
    await db_session.commit()

    response = await client.post(f"/repositories/{repo_id}/security-scan", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["files_scanned"] == 2
    rule_ids = {f["rule_id"] for f in body["findings"] if f["file_path"] == "app/config.py"}
    assert rule_ids == {"hardcoded-secret", "eval-exec"}
    # app/main.py's "def create_app(): ..." from _make_searchable_repository
    # is clean, so it shouldn't contribute any findings.
    assert all(f["file_path"] != "app/main.py" for f in body["findings"])


async def test_security_scan_returns_empty_for_clean_repository(client, db_session, monkeypatch):
    headers = await register_and_login(client, "scanner2@example.com")
    repo_id, _chunk = await _make_searchable_repository(client, db_session, monkeypatch, headers)

    response = await client.post(f"/repositories/{repo_id}/security-scan", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["findings"] == []
    assert body["files_scanned"] == 1


async def test_security_scan_not_found_for_other_user(client, db_session, monkeypatch):
    headers_a = await register_and_login(client, "scanowner@example.com")
    headers_b = await register_and_login(client, "scanintruder@example.com")
    repo_id, _chunk = await _make_searchable_repository(client, db_session, monkeypatch, headers_a)

    response = await client.post(f"/repositories/{repo_id}/security-scan", headers=headers_b)
    assert response.status_code == 404


async def test_security_scan_rejects_when_no_files(client, monkeypatch):
    headers = await register_and_login(client, "scanner3@example.com")
    _mock_fetch(monkeypatch, result=make_repo_info())
    created = await client.post(
        "/repositories", json={"github_url": "octocat/Hello-World"}, headers=headers
    )
    repo_id = created.json()["id"]

    response = await client.post(f"/repositories/{repo_id}/security-scan", headers=headers)
    assert response.status_code == 400


async def test_agent_requires_auth(client):
    response = await client.post(
        "/repositories/00000000-0000-0000-0000-000000000000/agent", json={"goal": "hi"}
    )
    assert response.status_code == 401


async def test_agent_finishes_immediately_without_tools(client, db_session, monkeypatch):
    headers = await register_and_login(client, "agent1@example.com")
    repo_id, _chunk = await _make_searchable_repository(client, db_session, monkeypatch, headers)

    async def _fake_generate_response(system_prompt, user_message, max_tokens=1024):
        return json.dumps({"action": "finish", "answer": "This repo has one file, app/main.py."})

    monkeypatch.setattr("app.services.agent.generate_response", _fake_generate_response)

    response = await client.post(
        f"/repositories/{repo_id}/agent", json={"goal": "what files exist?"}, headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "This repo has one file, app/main.py."
    assert body["steps"] == []


async def test_agent_calls_search_code_tool_then_finishes(client, db_session, monkeypatch):
    headers = await register_and_login(client, "agent2@example.com")
    repo_id, chunk = await _make_searchable_repository(client, db_session, monkeypatch, headers)

    calls = {"count": 0}

    async def _fake_generate_response(system_prompt, user_message, max_tokens=1024):
        calls["count"] += 1
        if calls["count"] == 1:
            return json.dumps(
                {"action": "tool", "tool": "search_code", "arguments": {"query": "app factory"}}
            )
        return json.dumps({"action": "finish", "answer": "The app factory lives in app/main.py."})

    async def _fake_embed(text):
        return [0.1, 0.2]

    async def _fake_search(vector, repository_id, limit=10):
        return [{"id": str(chunk.id), "score": 0.9, "payload": {"code_chunk_id": str(chunk.id)}}]

    monkeypatch.setattr("app.services.agent.generate_response", _fake_generate_response)
    monkeypatch.setattr("app.services.agent.generate_embedding", _fake_embed)
    monkeypatch.setattr("app.services.agent.vector_store.search", _fake_search)

    response = await client.post(
        f"/repositories/{repo_id}/agent", json={"goal": "how is the app created?"}, headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "The app factory lives in app/main.py."
    assert len(body["steps"]) == 1
    assert body["steps"][0]["tool"] == "search_code"
    assert "app/main.py" in body["steps"][0]["summary"]


async def test_agent_calls_explain_file_tool_then_finishes(client, db_session, monkeypatch):
    headers = await register_and_login(client, "agent3@example.com")
    repo_id, _chunk = await _make_searchable_repository(client, db_session, monkeypatch, headers)

    calls = {"count": 0}

    async def _fake_generate_response(system_prompt, user_message, max_tokens=1024):
        calls["count"] += 1
        if calls["count"] == 1:
            return json.dumps(
                {
                    "action": "tool",
                    "tool": "explain_file",
                    "arguments": {"file_path": "app/main.py"},
                }
            )
        if calls["count"] == 2:
            # explain_file's own internal LLM call, same mocked function.
            return "This file defines the FastAPI application factory."
        return json.dumps(
            {"action": "finish", "answer": "app/main.py defines the FastAPI app factory."}
        )

    monkeypatch.setattr("app.services.agent.generate_response", _fake_generate_response)

    response = await client.post(
        f"/repositories/{repo_id}/agent",
        json={"goal": "what does app/main.py do?"},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["steps"]) == 1
    assert body["steps"][0]["tool"] == "explain_file"
    assert body["steps"][0]["summary"] == "This file defines the FastAPI application factory."
    assert body["answer"] == "app/main.py defines the FastAPI app factory."


async def test_agent_calls_review_file_tool_then_finishes(client, db_session, monkeypatch):
    headers = await register_and_login(client, "agent7@example.com")
    repo_id, _chunk = await _make_searchable_repository(client, db_session, monkeypatch, headers)

    calls = {"count": 0}

    async def _fake_generate_response(system_prompt, user_message, max_tokens=1024):
        calls["count"] += 1
        if calls["count"] == 1:
            return json.dumps(
                {"action": "tool", "tool": "review_file", "arguments": {"file_path": "app/main.py"}}
            )
        if calls["count"] == 2:
            return "No issues found; the file is small and clean."
        return json.dumps({"action": "finish", "answer": "app/main.py looks clean."})

    monkeypatch.setattr("app.services.agent.generate_response", _fake_generate_response)

    response = await client.post(
        f"/repositories/{repo_id}/agent",
        json={"goal": "review app/main.py"},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["steps"]) == 1
    assert body["steps"][0]["tool"] == "review_file"
    assert body["steps"][0]["summary"] == "No issues found; the file is small and clean."
    assert body["answer"] == "app/main.py looks clean."


async def test_agent_calls_debug_tool_then_finishes(client, db_session, monkeypatch):
    headers = await register_and_login(client, "agent8@example.com")
    repo_id, chunk = await _make_searchable_repository(client, db_session, monkeypatch, headers)

    calls = {"count": 0}

    async def _fake_generate_response(system_prompt, user_message, max_tokens=1024):
        calls["count"] += 1
        if calls["count"] == 1:
            return json.dumps(
                {
                    "action": "tool",
                    "tool": "debug",
                    "arguments": {"description": "startup crashes with AttributeError"},
                }
            )
        if calls["count"] == 2:
            return "The crash happens because create_app() returns None."
        return json.dumps(
            {"action": "finish", "answer": "create_app() in app/main.py returns None."}
        )

    async def _fake_embed(text):
        return [0.1, 0.2]

    async def _fake_search(vector, repository_id, limit=10):
        return [{"id": str(chunk.id), "score": 0.9, "payload": {"code_chunk_id": str(chunk.id)}}]

    monkeypatch.setattr("app.services.agent.generate_response", _fake_generate_response)
    monkeypatch.setattr("app.services.agent.generate_embedding", _fake_embed)
    monkeypatch.setattr("app.services.agent.vector_store.search", _fake_search)

    response = await client.post(
        f"/repositories/{repo_id}/agent",
        json={"goal": "debug the startup crash"},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["steps"]) == 1
    assert body["steps"][0]["tool"] == "debug"
    assert body["steps"][0]["summary"] == "The crash happens because create_app() returns None."
    assert body["answer"] == "create_app() in app/main.py returns None."


async def test_agent_calls_architecture_tool_then_finishes(client, db_session, monkeypatch):
    headers = await register_and_login(client, "agent9@example.com")
    repo_id, _chunk = await _make_searchable_repository(client, db_session, monkeypatch, headers)

    calls = {"count": 0}

    async def _fake_generate_response(system_prompt, user_message, max_tokens=1024):
        calls["count"] += 1
        if calls["count"] == 1:
            return json.dumps({"action": "tool", "tool": "architecture", "arguments": {}})
        if calls["count"] == 2:
            assert "app/main.py" in user_message
            return "This is a small FastAPI application."
        return json.dumps({"action": "finish", "answer": "It's a small FastAPI application."})

    monkeypatch.setattr("app.services.agent.generate_response", _fake_generate_response)

    response = await client.post(
        f"/repositories/{repo_id}/agent",
        json={"goal": "summarize the architecture"},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["steps"]) == 1
    assert body["steps"][0]["tool"] == "architecture"
    assert body["steps"][0]["summary"] == "This is a small FastAPI application."
    assert body["answer"] == "It's a small FastAPI application."


async def test_agent_calls_security_scan_tool_then_finishes(client, db_session, monkeypatch):
    headers = await register_and_login(client, "agent10@example.com")
    repo_id, _chunk = await _make_searchable_repository(client, db_session, monkeypatch, headers)

    calls = {"count": 0}

    async def _fake_generate_response(system_prompt, user_message, max_tokens=1024):
        calls["count"] += 1
        if calls["count"] == 1:
            return json.dumps({"action": "tool", "tool": "security_scan", "arguments": {}})
        return json.dumps({"action": "finish", "answer": "No security issues found."})

    monkeypatch.setattr("app.services.agent.generate_response", _fake_generate_response)

    response = await client.post(
        f"/repositories/{repo_id}/agent",
        json={"goal": "check for security issues"},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    # security_scan needs no LLM/embedding call at all, so this is only 2
    # generate_response calls total (plan -> tool -> plan -> finish), not 3
    # like the other tools that make their own internal LLM call.
    assert calls["count"] == 2
    assert len(body["steps"]) == 1
    assert body["steps"][0]["tool"] == "security_scan"
    assert body["steps"][0]["summary"] == "security_scan found no issues."
    assert body["answer"] == "No security issues found."


async def test_agent_forces_finish_after_default_max_steps(client, db_session, monkeypatch):
    headers = await register_and_login(client, "agent4@example.com")
    repo_id, _chunk = await _make_searchable_repository(client, db_session, monkeypatch, headers)

    async def _fake_generate_response(system_prompt, user_message, max_tokens=1024):
        return json.dumps({"action": "tool", "tool": "does_not_exist", "arguments": {}})

    monkeypatch.setattr("app.services.agent.generate_response", _fake_generate_response)

    response = await client.post(
        f"/repositories/{repo_id}/agent", json={"goal": "loop forever"}, headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["steps"]) == 4  # AgentRequest.max_steps default
    assert body["answer"] != ""


async def test_agent_respects_custom_max_steps(client, db_session, monkeypatch):
    headers = await register_and_login(client, "agent11@example.com")
    repo_id, _chunk = await _make_searchable_repository(client, db_session, monkeypatch, headers)

    async def _fake_generate_response(system_prompt, user_message, max_tokens=1024):
        return json.dumps({"action": "tool", "tool": "does_not_exist", "arguments": {}})

    monkeypatch.setattr("app.services.agent.generate_response", _fake_generate_response)

    response = await client.post(
        f"/repositories/{repo_id}/agent",
        json={"goal": "loop forever", "max_steps": 2},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["steps"]) == 2
    assert body["answer"] != ""


async def test_agent_rejects_out_of_range_max_steps(client, db_session, monkeypatch):
    headers = await register_and_login(client, "agent12@example.com")
    repo_id, _chunk = await _make_searchable_repository(client, db_session, monkeypatch, headers)

    response = await client.post(
        f"/repositories/{repo_id}/agent",
        json={"goal": "hi", "max_steps": 11},
        headers=headers,
    )
    assert response.status_code == 422

    response = await client.post(
        f"/repositories/{repo_id}/agent",
        json={"goal": "hi", "max_steps": 0},
        headers=headers,
    )
    assert response.status_code == 422


async def test_agent_not_found_for_other_user(client, db_session, monkeypatch):
    headers_a = await register_and_login(client, "agentowner@example.com")
    headers_b = await register_and_login(client, "agentintruder@example.com")
    repo_id, _chunk = await _make_searchable_repository(client, db_session, monkeypatch, headers_a)

    response = await client.post(
        f"/repositories/{repo_id}/agent", json={"goal": "hi"}, headers=headers_b
    )
    assert response.status_code == 404


async def test_agent_maps_llm_config_error_to_503(client, db_session, monkeypatch):
    headers = await register_and_login(client, "agent5@example.com")
    repo_id, _chunk = await _make_searchable_repository(client, db_session, monkeypatch, headers)

    async def _fake_generate_response(system_prompt, user_message, max_tokens=1024):
        raise LLMConfigError("ANTHROPIC_API_KEY is not configured")

    monkeypatch.setattr("app.services.agent.generate_response", _fake_generate_response)

    response = await client.post(
        f"/repositories/{repo_id}/agent", json={"goal": "anything"}, headers=headers
    )
    assert response.status_code == 503


async def test_agent_rejects_empty_goal(client, db_session, monkeypatch):
    headers = await register_and_login(client, "agent6@example.com")
    repo_id, _chunk = await _make_searchable_repository(client, db_session, monkeypatch, headers)

    response = await client.post(
        f"/repositories/{repo_id}/agent", json={"goal": ""}, headers=headers
    )
    assert response.status_code == 422
