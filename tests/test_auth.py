import uuid

import httpx

from tests.conftest import register_and_login


async def test_register_login_me(client: httpx.AsyncClient) -> None:
    email = f"u-{uuid.uuid4().hex[:8]}@example.com"
    r = await client.post("/auth/register", json={"email": email, "password": "hunter22hunter22"})
    assert r.status_code == 201
    assert r.json()["email"] == email

    r = await client.post("/auth/login", json={"email": email, "password": "hunter22hunter22"})
    assert r.status_code == 200
    token = r.json()["access_token"]
    assert r.json()["token_type"] == "bearer"

    r = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["email"] == email


async def test_duplicate_email_returns_409(client: httpx.AsyncClient) -> None:
    email = f"u-{uuid.uuid4().hex[:8]}@example.com"
    await client.post("/auth/register", json={"email": email, "password": "hunter22hunter22"})
    r = await client.post("/auth/register", json={"email": email, "password": "hunter22hunter22"})
    assert r.status_code == 409
    assert r.json()["error"] == "email_already_registered"


async def test_wrong_password_returns_401(client: httpx.AsyncClient) -> None:
    _, _ = await register_and_login(client, email="pw@example.com", password="hunter22hunter22")
    r = await client.post("/auth/login", json={"email": "pw@example.com", "password": "nope"})
    assert r.status_code == 401
    assert r.json()["error"] == "invalid_credentials"


async def test_missing_token_returns_401(client: httpx.AsyncClient) -> None:
    r = await client.get("/auth/me")
    assert r.status_code in (401, 403)


async def test_malformed_token_returns_401(client: httpx.AsyncClient) -> None:
    r = await client.get("/auth/me", headers={"Authorization": "Bearer not-a-real-jwt"})
    assert r.status_code == 401
