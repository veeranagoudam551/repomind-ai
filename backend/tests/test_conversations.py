import uuid

from sqlalchemy import event

from app.models.code_chunk import CodeChunk
from app.models.conversation import Conversation
from app.models.message import Message, MessageRole
from app.models.repository_file import RepositoryFile
from app.services.embeddings import EmbeddingConfigError
from app.services.llm import LLMConfigError
from tests.conftest import register_and_login
from tests.factories import make_repo_info
from tests.test_repositories import _mock_embed_query, _mock_fetch


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

    _mock_embed_query(monkeypatch, "app.api.conversations.get_embedding_provider", _fake_embed)
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

    _mock_embed_query(monkeypatch, "app.api.conversations.get_embedding_provider", _fake_embed)
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

    _mock_embed_query(monkeypatch, "app.api.conversations.get_embedding_provider", _fake_embed)

    response = await client.post(
        f"/conversations/{conversation_id}/messages", json={"content": "hi"}, headers=headers
    )
    assert response.status_code == 503


async def _add_message(db_session, conversation_id, role, content, source_chunk_ids=None):
    # One add()+commit() per message, not a batched add_all() - Postgres's
    # now() (this table's created_at server_default) returns the *current
    # transaction's* start time, so messages committed together would tie
    # on created_at and make list_messages' ORDER BY created_at ambiguous
    # between them.
    message = Message(
        conversation_id=conversation_id,
        role=role,
        content=content,
        source_chunk_ids=source_chunk_ids,
    )
    db_session.add(message)
    await db_session.commit()
    await db_session.refresh(message)
    return message


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

    _mock_embed_query(monkeypatch, "app.api.conversations.get_embedding_provider", _fake_embed)
    monkeypatch.setattr("app.api.conversations.vector_store.search", _fake_search)
    monkeypatch.setattr("app.api.conversations.generate_response", _fake_generate_response)

    response = await client.post(
        f"/conversations/{conversation_id}/messages", json={"content": "hi"}, headers=headers
    )
    assert response.status_code == 503


async def test_list_messages_batches_source_chunk_lookups(
    client, db_session, test_engine, monkeypatch
):
    # Day 57 regression test: GET .../messages used to call _fetch_chunks
    # once per message with any source_chunk_ids (one code_chunks query per
    # message - the N+1 pattern the Day 57 audit found), instead of once for
    # the whole conversation. Counting statements that touch the
    # code_chunks table specifically (rather than the total query count)
    # keeps this robust against unrelated queries - the conversation-
    # ownership check, the messages SELECT itself, etc.
    headers = await register_and_login(client, "msgbatch@example.com")
    repo_id, chunk_a = await _make_repo_with_chunk(client, db_session, monkeypatch, headers)

    repo_file_b = RepositoryFile(repository_id=repo_id, file_path="app/utils.py", size_bytes=10)
    db_session.add(repo_file_b)
    await db_session.commit()
    await db_session.refresh(repo_file_b)

    chunk_b = CodeChunk(
        repository_id=repo_id,
        repository_file_id=repo_file_b.id,
        chunk_index=0,
        content="def helper(): ...",
        start_line=1,
        end_line=1,
        vector_id="chunk-b-vector",
    )
    db_session.add(chunk_b)
    await db_session.commit()
    await db_session.refresh(chunk_b)

    conversation_id = await _make_conversation(client, repo_id, headers)

    missing_chunk_id = str(uuid.uuid4())
    await _add_message(db_session, conversation_id, MessageRole.USER, "q1")
    await _add_message(
        db_session, conversation_id, MessageRole.ASSISTANT, "a1", [str(chunk_a.id)]
    )
    await _add_message(db_session, conversation_id, MessageRole.USER, "q2")
    await _add_message(
        # Shares chunk_a with the first assistant message, so the batched
        # lookup must also deduplicate correctly, not just avoid per-message
        # queries.
        db_session, conversation_id, MessageRole.ASSISTANT, "a2",
        [str(chunk_a.id), str(chunk_b.id)],
    )
    await _add_message(db_session, conversation_id, MessageRole.USER, "q3")
    await _add_message(db_session, conversation_id, MessageRole.ASSISTANT, "a3", [])
    await _add_message(db_session, conversation_id, MessageRole.USER, "q4")
    await _add_message(
        # A source_chunk_id that no longer exists in code_chunks (e.g. the
        # repository was reindexed since) - must be silently omitted, not
        # an error.
        db_session, conversation_id, MessageRole.ASSISTANT, "a4", [missing_chunk_id]
    )

    code_chunk_statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        if "code_chunks" in statement:
            code_chunk_statements.append(statement)

    event.listen(test_engine.sync_engine, "before_cursor_execute", _record)
    try:
        response = await client.get(f"/conversations/{conversation_id}/messages", headers=headers)
    finally:
        event.remove(test_engine.sync_engine, "before_cursor_execute", _record)

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 8

    # Exactly one query against code_chunks for the whole conversation -
    # not one per message with non-empty source_chunk_ids (which would be
    # 3 here: a1, a2, and a4).
    assert len(code_chunk_statements) == 1

    by_content = {message["content"]: message for message in body}
    assert set(by_content) == {"q1", "a1", "q2", "a2", "q3", "a3", "q4", "a4"}

    assert [source["code_chunk_id"] for source in by_content["a1"]["sources"]] == [str(chunk_a.id)]
    assert {source["code_chunk_id"] for source in by_content["a2"]["sources"]} == {
        str(chunk_a.id),
        str(chunk_b.id),
    }
    assert by_content["a3"]["sources"] == []
    assert by_content["a4"]["sources"] == []
    assert by_content["q1"]["sources"] == []
