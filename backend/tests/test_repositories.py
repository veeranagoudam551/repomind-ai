import uuid

from sqlalchemy import select

from app.models.code_chunk import CodeChunk
from app.models.repository import Repository, RepositoryStatus
from app.models.repository_file import RepositoryFile
from app.services.github import GitHubAPIError, GitHubRepoNotFound
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

    async def _spy(repository_id):
        calls.append(repository_id)

    monkeypatch.setattr("app.api.repositories.ingest_repository", _spy)

    response = await client.post(f"/repositories/{repo_id}/reindex", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending"
    assert body["error_message"] is None
    assert calls == [repo_id]


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
