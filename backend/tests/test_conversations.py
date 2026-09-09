import uuid

from app.models.code_chunk import CodeChunk
from app.models.conversation import Conversation
from app.models.repository_file import RepositoryFile
from app.services.embeddings import EmbeddingConfigError
from app.services.llm import LLMConfigError
from tests.conftest import register_and_login
from tests.factories import make_repo_info
from tests.test_repositories import _mock_fetch


async def _make_repo_with_chunk(client, db_session, monkeypatch, headers):
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


async def _make_conversation(client, repo_id, headers) -> uuid.UUID:
    response = await client.post(
        f"/repositories/{repo_id}/conversations", json={"title": "Test chat"}, headers=headers
    )
    assert response.status_code == 201
    return uuid.UUID(response.json()["id"])


async def test_create_conversation_requires_auth(client):
    response = await client.post(
        "/repositories/00000000-0000-0000-0000-000000000000/conversations", json={}
    )
    assert response.status_code == 401


async def test_create_conversation_success(client, db_session, monkeypatch):
    headers = await register_and_login(client, "convo1@example.com")
    repo_id, _chunk = await _make_repo_with_chunk(client, db_session, monkeypatch, headers)

    response = await client.post(
        f"/repositories/{repo_id}/conversations", json={"title": "My chat"}, headers=headers
    )
    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "My chat"
    assert body["repository_id"] == str(repo_id)


async def test_create_conversation_not_found_for_other_user_repo(client, db_session, monkeypatch):
    headers_a = await register_and_login(client, "convoowner@example.com")
    headers_b = await register_and_login(client, "convointruder@example.com")
    repo_id, _chunk = await _make_repo_with_chunk(client, db_session, monkeypatch, headers_a)

    response = await client.post(
        f"/repositories/{repo_id}/conversations", json={}, headers=headers_b
    )
    assert response.status_code == 404


async def test_list_conversations_only_shows_own(client, db_session, monkeypatch):
    headers_a = await register_and_login(client, "convolist1@example.com")
    headers_b = await register_and_login(client, "convolist2@example.com")
    repo_id, _chunk = await _make_repo_with_chunk(client, db_session, monkeypatch, headers_a)

    await _make_conversation(client, repo_id, headers_a)

    response_a = await client.get(f"/repositories/{repo_id}/conversations", headers=headers_a)
    assert response_a.status_code == 200
    assert len(response_a.json()) == 1

    response_b = await client.get(f"/repositories/{repo_id}/conversations", headers=headers_b)
    assert response_b.status_code == 404


async def test_send_message_requires_auth(client):
    response = await client.post(
        "/conversations/00000000-0000-0000-0000-000000000000/messages", json={"content": "hi"}
    )
    assert response.status_code == 401


async def test_send_message_not_found_for_other_user(client, db_session, monkeypatch):
    headers_a = await register_and_login(client, "msgowner@example.com")
    headers_b = await register_and_login(client, "msgintruder@example.com")
    repo_id, _chunk = await _make_repo_with_chunk(client, db_session, monkeypatch, headers_a)
    conversation_id = await _make_conversation(client, repo_id, headers_a)

    response = await client.post(
        f"/conversations/{conversation_id}/messages", json={"content": "hi"}, headers=headers_b
    )
    assert response.status_code == 404


async def test_send_message_full_rag_flow(client, db_session, monkeypatch):
    headers = await register_and_login(client, "msgflow@example.com")
    repo_id, chunk = await _make_repo_with_chunk(client, db_session, monkeypatch, headers)
    conversation_id = await _make_conversation(client, repo_id, headers)

    async def _fake_embed(text):
        return [0.1, 0.2]

    async def _fake_search(vector, repository_id, limit=10):
        return [{"id": str(chunk.id), "score": 0.9, "payload": {"code_chunk_id": str(chunk.id)}}]

    llm_calls = []

    async def _fake_generate_response(system_prompt, user_message, max_tokens=1024):
        llm_calls.append((system_prompt, user_message))
        return "The app is created by calling create_app()."

    monkeypatch.setattr("app.api.conversations.generate_embedding", _fake_embed)
    monkeypatch.setattr("app.api.conversations.vector_store.search", _fake_search)
    monkeypatch.setattr("app.api.conversations.generate_response", _fake_generate_response)

    response = await client.post(
        f"/conversations/{conversation_id}/messages",
        json={"content": "how is the app created?"},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "assistant"
    assert body["content"] == "The app is created by calling create_app()."
    assert len(body["sources"]) == 1
    assert body["sources"][0]["code_chunk_id"] == str(chunk.id)
    assert body["sources"][0]["file_path"] == "app/main.py"

    assert len(llm_calls) == 1
    assert "how is the app created?" in llm_calls[0][1]
    assert "def create_app()" in llm_calls[0][1]

    messages_response = await client.get(
        f"/conversations/{conversation_id}/messages", headers=headers
    )
    assert messages_response.status_code == 200
    messages = messages_response.json()
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "how is the app created?"
    assert messages[1]["role"] == "assistant"
    assert messages[1]["sources"][0]["file_path"] == "app/main.py"


async def test_send_message_returns_canned_reply_when_no_hits(client, db_session, monkeypatch):
    headers = await register_and_login(client, "msgnohits@example.com")
    repo_id, _chunk = await _make_repo_with_chunk(client, db_session, monkeypatch, headers)
    conversation_id = await _make_conversation(client, repo_id, headers)

    async def _fake_embed(text):
        return [0.1, 0.2]

    async def _fake_search(vector, repository_id, limit=10):
        return []

    async def _fail_if_called(*args, **kwargs):
        raise AssertionError("LLM should not be called when there is no retrieved context")

    monkeypatch.setattr("app.api.conversations.generate_embedding", _fake_embed)
    monkeypatch.setattr("app.api.conversations.vector_store.search", _fake_search)
    monkeypatch.setattr("app.api.conversations.generate_response", _fail_if_called)

    response = await client.post(
        f"/conversations/{conversation_id}/messages",
        json={"content": "anything"},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["sources"] == []
    assert "couldn't find any indexed code" in body["content"]


async def test_send_message_maps_embedding_config_error_to_503(client, db_session, monkeypatch):
    headers = await register_and_login(client, "msgembederror@example.com")
    repo_id, _chunk = await _make_repo_with_chunk(client, db_session, monkeypatch, headers)
    conversation_id = await _make_conversation(client, repo_id, headers)

    async def _fake_embed(text):
        raise EmbeddingConfigError("OPENAI_API_KEY is not configured")

    monkeypatch.setattr("app.api.conversations.generate_embedding", _fake_embed)

    response = await client.post(
        f"/conversations/{conversation_id}/messages", json={"content": "hi"}, headers=headers
    )
    assert response.status_code == 503


async def test_send_message_maps_llm_config_error_to_503(client, db_session, monkeypatch):
    headers = await register_and_login(client, "msgllmerror@example.com")
    repo_id, chunk = await _make_repo_with_chunk(client, db_session, monkeypatch, headers)
    conversation_id = await _make_conversation(client, repo_id, headers)

    async def _fake_embed(text):
        return [0.1, 0.2]

    async def _fake_search(vector, repository_id, limit=10):
        return [{"id": str(chunk.id), "score": 0.9, "payload": {"code_chunk_id": str(chunk.id)}}]

    async def _fake_generate_response(system_prompt, user_message, max_tokens=1024):
        raise LLMConfigError("ANTHROPIC_API_KEY is not configured")

    monkeypatch.setattr("app.api.conversations.generate_embedding", _fake_embed)
    monkeypatch.setattr("app.api.conversations.vector_store.search", _fake_search)
    monkeypatch.setattr("app.api.conversations.generate_response", _fake_generate_response)

    response = await client.post(
        f"/conversations/{conversation_id}/messages", json={"content": "hi"}, headers=headers
    )
    assert response.status_code == 503
