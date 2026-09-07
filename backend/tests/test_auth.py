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
