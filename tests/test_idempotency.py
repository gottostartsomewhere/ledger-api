"""Idempotency contract:
  - replaying same (user, key) with same payload → same response, no duplicate
  - replaying same (user, key) with different payload → 409 conflict
  - concurrent requests with same key → at most one transfer, both return same body
"""
from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import httpx

from tests.conftest import auth_headers, register_and_login


async def _setup(client: httpx.AsyncClient) -> tuple[str, str]:
    _, token = await register_and_login(client, email=f"idem-{uuid.uuid4().hex[:6]}@e.com")
    r = await client.post("/accounts", headers=auth_headers(token), json={"currency": "USD"})
    acct = r.json()["id"]
    await client.post(
        "/transactions/deposit",
        headers=auth_headers(token, idempotency_key=str(uuid.uuid4())),
        json={"account_id": acct, "amount": "1000.00"},
    )
    return token, acct


async def test_replay_same_key_returns_cached_response(client: httpx.AsyncClient) -> None:
    token, acct = await _setup(client)
    key = str(uuid.uuid4())
    body = {"account_id": acct, "amount": "25.00"}

    r1 = await client.post(
        "/transactions/withdraw",
        headers=auth_headers(token, idempotency_key=key),
        json=body,
    )
    r2 = await client.post(
        "/transactions/withdraw",
        headers=auth_headers(token, idempotency_key=key),
        json=body,
    )
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["id"] == r2.json()["id"]

    r = await client.get(f"/accounts/{acct}", headers=auth_headers(token))
    assert Decimal(r.json()["balance"]) == Decimal("975.0000")


async def test_replay_same_key_different_payload_409(client: httpx.AsyncClient) -> None:
    token, acct = await _setup(client)
    key = str(uuid.uuid4())

    r1 = await client.post(
        "/transactions/withdraw",
        headers=auth_headers(token, idempotency_key=key),
        json={"account_id": acct, "amount": "10.00"},
    )
    assert r1.status_code == 201

    r2 = await client.post(
        "/transactions/withdraw",
        headers=auth_headers(token, idempotency_key=key),
        json={"account_id": acct, "amount": "99.00"},
    )
    assert r2.status_code == 409
    assert r2.json()["error"] == "idempotency_key_conflict"


async def test_concurrent_requests_same_key_deduped(client: httpx.AsyncClient) -> None:
    token, acct = await _setup(client)
    key = str(uuid.uuid4())
    body = {"account_id": acct, "amount": "40.00"}

    coros = [
        client.post(
            "/transactions/withdraw",
            headers=auth_headers(token, idempotency_key=key),
            json=body,
        )
        for _ in range(5)
    ]
    results = await asyncio.gather(*coros, return_exceptions=True)
    successes = [r for r in results if isinstance(r, httpx.Response) and r.status_code == 201]
    assert len(successes) == 5  # all 5 should appear successful
    # ...and they should all reference the same transfer id.
    ids = {r.json()["id"] for r in successes}
    assert len(ids) == 1, f"expected one transfer, got {ids}"

    r = await client.get(f"/accounts/{acct}", headers=auth_headers(token))
    assert Decimal(r.json()["balance"]) == Decimal("960.0000")  # 1000 - 40
