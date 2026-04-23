"""Test fixtures. Spins up real Postgres + Redis via testcontainers — we do
NOT mock the DB, because the ledger's correctness guarantees live in the DB
(transactions, CHECK constraints, SELECT FOR UPDATE)."""
from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncGenerator, Generator

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer


@pytest.fixture(scope="session")
def postgres_container() -> Generator[PostgresContainer, None, None]:
    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as pg:
        yield pg


@pytest.fixture(scope="session")
def redis_container() -> Generator[RedisContainer, None, None]:
    with RedisContainer("redis:7-alpine") as r:
        yield r


@pytest.fixture(scope="session", autouse=True)
def _env(postgres_container: PostgresContainer, redis_container: RedisContainer):
    redis_host = redis_container.get_container_host_ip()
    redis_port = redis_container.get_exposed_port(6379)
    os.environ["DATABASE_URL"] = postgres_container.get_connection_url()
    os.environ["REDIS_URL"] = f"redis://{redis_host}:{redis_port}/0"
    os.environ["JWT_SECRET"] = "test-secret-" + uuid.uuid4().hex
    os.environ["JWT_ALGORITHM"] = "HS256"
    os.environ["JWT_ACCESS_TTL_MINUTES"] = "60"
    os.environ["RATE_LIMIT_PER_MINUTE"] = "10000"
    os.environ["APP_ENV"] = "test"

    from app.config import get_settings
    get_settings.cache_clear()
    yield


@pytest.fixture(scope="session")
async def _engine():
    from app.config import get_settings
    from app.database import Base
    from app.models import account, transaction, user  # noqa: F401

    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db(_engine) -> AsyncGenerator[AsyncSession, None]:
    """Fresh session per test; truncates all tables before each test so they
    start from a known-empty state."""
    from sqlalchemy import text

    from app.database import Base

    maker = async_sessionmaker(_engine, expire_on_commit=False)
    async with _engine.begin() as conn:
        tables = ", ".join(
            f'"{t.name}"' for t in reversed(Base.metadata.sorted_tables)
        )
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))

    async with maker() as session:
        yield session


@pytest.fixture
async def client(_engine) -> AsyncGenerator[httpx.AsyncClient, None]:
    from app.database import get_db
    from app.main import app

    maker = async_sessionmaker(_engine, expire_on_commit=False)

    async def override_get_db():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def register_and_login(
    client: httpx.AsyncClient, email: str | None = None, password: str = "hunter22hunter22"
) -> tuple[dict, str]:
    email = email or f"user-{uuid.uuid4().hex[:8]}@example.com"
    r = await client.post("/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    user = r.json()
    r = await client.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    return user, token


def auth_headers(token: str, idempotency_key: str | None = None) -> dict[str, str]:
    h = {"Authorization": f"Bearer {token}"}
    if idempotency_key:
        h["Idempotency-Key"] = idempotency_key
    return h
