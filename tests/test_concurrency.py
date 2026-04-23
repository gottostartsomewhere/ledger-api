"""Concurrency test: 20 simultaneous withdrawals against a $100 account, each
for $10. If SELECT FOR UPDATE works, exactly 10 succeed and 10 fail, the
balance is exactly $0, and the sum of successful debits equals the starting
balance. Any double-spend would produce a negative balance or would crash the
CHECK constraint — either way this test catches it."""
from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import httpx

from tests.conftest import auth_headers, register_and_login


async def test_concurrent_withdrawals_never_overdraw(client: httpx.AsyncClient) -> None:
    _, token = await register_and_login(client, email="concur@example.com")
    r = await client.post("/accounts", headers=auth_headers(token), json={"currency": "USD"})
    acct = r.json()["id"]
    await client.post(
        "/transactions/deposit",
        headers=auth_headers(token, idempotency_key=str(uuid.uuid4())),
        json={"account_id": acct, "amount": "100.00"},
    )

    async def withdraw_once() -> int:
        r = await client.post(
            "/transactions/withdraw",
            headers=auth_headers(token, idempotency_key=str(uuid.uuid4())),
            json={"account_id": acct, "amount": "10.00"},
        )
        return r.status_code

    codes = await asyncio.gather(*[withdraw_once() for _ in range(20)])
    successes = sum(1 for c in codes if c == 201)
    failures = sum(1 for c in codes if c == 422)
    assert successes + failures == 20
    assert successes == 10, f"expected 10 successes, got {successes} ({codes})"

    r = await client.get(f"/accounts/{acct}", headers=auth_headers(token))
    assert Decimal(r.json()["balance"]) == Decimal("0.0000")


async def test_concurrent_transfers_between_pair_no_deadlock(client: httpx.AsyncClient) -> None:
    """Two users transfer back and forth concurrently. With ordered row locks
    this should complete without deadlock; balances must remain conserved."""
    _, alice = await register_and_login(client, email="ca@example.com")
    _, bob = await register_and_login(client, email="cb@example.com")
    a = (await client.post("/accounts", headers=auth_headers(alice), json={"currency": "USD"})).json()["id"]
    b = (await client.post("/accounts", headers=auth_headers(bob), json={"currency": "USD"})).json()["id"]
    await client.post(
        "/transactions/deposit",
        headers=auth_headers(alice, idempotency_key=str(uuid.uuid4())),
        json={"account_id": a, "amount": "500.00"},
    )
    await client.post(
        "/transactions/deposit",
        headers=auth_headers(bob, idempotency_key=str(uuid.uuid4())),
        json={"account_id": b, "amount": "500.00"},
    )

    async def a_to_b() -> int:
        r = await client.post(
            "/transactions/transfer",
            headers=auth_headers(alice, idempotency_key=str(uuid.uuid4())),
            json={"from_account_id": a, "to_account_id": b, "amount": "1.00"},
        )
        return r.status_code

    async def b_to_a() -> int:
        r = await client.post(
            "/transactions/transfer",
            headers=auth_headers(bob, idempotency_key=str(uuid.uuid4())),
            json={"from_account_id": b, "to_account_id": a, "amount": "1.00"},
        )
        return r.status_code

    tasks = []
    for _ in range(15):
        tasks.append(a_to_b())
        tasks.append(b_to_a())
    codes = await asyncio.gather(*tasks)
    assert all(c == 201 for c in codes), codes

    bal_a = Decimal((await client.get(f"/accounts/{a}", headers=auth_headers(alice))).json()["balance"])
    bal_b = Decimal((await client.get(f"/accounts/{b}", headers=auth_headers(bob))).json()["balance"])
    assert bal_a + bal_b == Decimal("1000.0000")
