import asyncio

from app.core.security import hash_password, verify_password
from tests.conftest import register_and_login


async def test_register_success(client):
    response = await client.post(
        "/auth/register",
        json={"email": "alice@example.com", "password": "TestPass123!", "full_name": "Alice"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "alice@example.com"
    assert body["full_name"] == "Alice"
    assert body["is_active"] is True
    assert "hashed_password" not in body


async def test_register_duplicate_email(client):
    payload = {"email": "bob@example.com", "password": "TestPass123!"}
    first = await client.post("/auth/register", json=payload)
    second = await client.post("/auth/register", json=payload)
    assert first.status_code == 201
    assert second.status_code == 400


async def test_register_rejects_short_password(client):
    response = await client.post(
        "/auth/register", json={"email": "carol@example.com", "password": "short"}
    )
    assert response.status_code == 422


async def test_login_success(client):
    await client.post(
        "/auth/register", json={"email": "dave@example.com", "password": "TestPass123!"}
    )
    response = await client.post(
        "/auth/login", json={"email": "dave@example.com", "password": "TestPass123!"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]


async def test_login_wrong_password(client):
    await client.post(
        "/auth/register", json={"email": "erin@example.com", "password": "TestPass123!"}
    )
    response = await client.post(
        "/auth/login", json={"email": "erin@example.com", "password": "WrongPass123!"}
    )
    assert response.status_code == 401


async def test_login_nonexistent_user(client):
    response = await client.post(
        "/auth/login", json={"email": "ghost@example.com", "password": "TestPass123!"}
    )
    assert response.status_code == 401


async def test_me_requires_token(client):
    response = await client.get("/auth/me")
    assert response.status_code == 401


async def test_me_with_valid_token(client):
    headers = await register_and_login(client, "frank@example.com")
    response = await client.get("/auth/me", headers=headers)
    assert response.status_code == 200
    assert response.json()["email"] == "frank@example.com"


async def test_me_rejects_garbage_token(client):
    response = await client.get("/auth/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert response.status_code == 401


def _track_to_thread(monkeypatch, target: str) -> list:
    """Wraps asyncio.to_thread so a test can assert *which* function it was
    called with (Day 57's event-loop-offload fix) without asserting
    anything about timing - the wrapper still awaits the real
    asyncio.to_thread, so the wrapped call's actual behavior (a real bcrypt
    hash/verify) is completely unchanged."""
    calls: list = []
    real_to_thread = asyncio.to_thread

    async def _tracking_to_thread(func, *args, **kwargs):
        calls.append(func)
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(target, _tracking_to_thread)
    return calls


async def test_register_offloads_bcrypt_hashing_to_a_thread(client, monkeypatch):
    calls = _track_to_thread(monkeypatch, "app.api.auth.asyncio.to_thread")

    response = await client.post(
        "/auth/register",
        json={"email": "threaded_register@example.com", "password": "TestPass123!"},
    )

    assert response.status_code == 201
    assert calls == [hash_password]


async def test_login_offloads_bcrypt_verification_to_a_thread(client, monkeypatch):
    await client.post(
        "/auth/register",
        json={"email": "threaded_login@example.com", "password": "TestPass123!"},
    )

    calls = _track_to_thread(monkeypatch, "app.api.auth.asyncio.to_thread")

    response = await client.post(
        "/auth/login",
        json={"email": "threaded_login@example.com", "password": "TestPass123!"},
    )

    assert response.status_code == 200
    assert calls == [verify_password]


async def test_login_wrong_password_still_offloads_verification(client, monkeypatch):
    # The short-circuit (`user is None or not await asyncio.to_thread(...)`)
    # must still call verify_password - via the thread - whenever a user
    # was actually found, wrong password or not, matching the pre-fix
    # behavior exactly (test_login_wrong_password above already covers the
    # resulting 401; this covers that the offload path is what produced it).
    await client.post(
        "/auth/register",
        json={"email": "threaded_wrongpass@example.com", "password": "TestPass123!"},
    )

    calls = _track_to_thread(monkeypatch, "app.api.auth.asyncio.to_thread")

    response = await client.post(
        "/auth/login",
        json={"email": "threaded_wrongpass@example.com", "password": "WrongPass123!"},
    )

    assert response.status_code == 401
    assert calls == [verify_password]


async def test_login_nonexistent_user_never_calls_to_thread(client, monkeypatch):
    # The `or` short-circuit means verify_password (and so asyncio.to_thread)
    # must never run at all when there's no user to check a password
    # against - there's no hashed_password to pass it.
    calls = _track_to_thread(monkeypatch, "app.api.auth.asyncio.to_thread")

    response = await client.post(
        "/auth/login",
        json={"email": "no_such_threaded_user@example.com", "password": "TestPass123!"},
    )

    assert response.status_code == 401
    assert calls == []
