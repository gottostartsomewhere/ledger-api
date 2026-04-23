"""Tests for the core ledger invariants:
  1. Every transfer produces balanced (sum debit == sum credit) entries.
  2. Balances never go negative on USER accounts.
  3. Cross-user and cross-currency transfers are rejected.
  4. Same-account transfers are rejected.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import auth_headers, register_and_login


async def _create_account(
    client: httpx.AsyncClient, token: str, currency: str = "USD"
) -> str:
    r = await client.post(
        "/accounts",
        headers=auth_headers(token),
        json={"currency": currency},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _deposit(
    client: httpx.AsyncClient, token: str, account_id: str, amount: str
) -> dict:
    r = await client.post(
        "/transactions/deposit",
        headers=auth_headers(token, idempotency_key=str(uuid.uuid4())),
        json={"account_id": account_id, "amount": amount},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_deposit_produces_balanced_double_entry(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    _, token = await register_and_login(client)
    acct_id = await _create_account(client, token)

    transfer = await _deposit(client, token, acct_id, "100.00")
    entries = transfer["entries"]
    assert len(entries) == 2
    debits = [e for e in entries if e["entry_type"] == "DEBIT"]
    credits = [e for e in entries if e["entry_type"] == "CREDIT"]
    assert len(debits) == 1 and len(credits) == 1
    assert Decimal(debits[0]["amount"]) == Decimal(credits[0]["amount"]) == Decimal("100.00")
    assert credits[0]["account_id"] == acct_id

    r = await client.get(f"/accounts/{acct_id}", headers=auth_headers(token))
    assert Decimal(r.json()["balance"]) == Decimal("100.0000")


async def test_withdraw_without_funds_returns_422(client: httpx.AsyncClient) -> None:
    _, token = await register_and_login(client)
    acct_id = await _create_account(client, token)

    r = await client.post(
        "/transactions/withdraw",
        headers=auth_headers(token, idempotency_key=str(uuid.uuid4())),
        json={"account_id": acct_id, "amount": "10.00"},
    )
    assert r.status_code == 422
    assert r.json()["error"] == "insufficient_funds"


async def test_withdraw_succeeds_then_insufficient(client: httpx.AsyncClient) -> None:
    _, token = await register_and_login(client)
    acct_id = await _create_account(client, token)
    await _deposit(client, token, acct_id, "50.00")

    r = await client.post(
        "/transactions/withdraw",
        headers=auth_headers(token, idempotency_key=str(uuid.uuid4())),
        json={"account_id": acct_id, "amount": "30.00"},
    )
    assert r.status_code == 201

    r = await client.get(f"/accounts/{acct_id}", headers=auth_headers(token))
    assert Decimal(r.json()["balance"]) == Decimal("20.0000")

    r = await client.post(
        "/transactions/withdraw",
        headers=auth_headers(token, idempotency_key=str(uuid.uuid4())),
        json={"account_id": acct_id, "amount": "100.00"},
    )
    assert r.status_code == 422


async def test_transfer_between_users_moves_money(client: httpx.AsyncClient) -> None:
    _, alice = await register_and_login(client, email="alice2@example.com")
    _, bob = await register_and_login(client, email="bob2@example.com")
    a = await _create_account(client, alice)
    b = await _create_account(client, bob)
    await _deposit(client, alice, a, "200.00")

    r = await client.post(
        "/transactions/transfer",
        headers=auth_headers(alice, idempotency_key=str(uuid.uuid4())),
        json={"from_account_id": a, "to_account_id": b, "amount": "75.00"},
    )
    assert r.status_code == 201, r.text

    assert Decimal((await client.get(f"/accounts/{a}", headers=auth_headers(alice))).json()["balance"]) == Decimal("125.0000")
    assert Decimal((await client.get(f"/accounts/{b}", headers=auth_headers(bob))).json()["balance"]) == Decimal("75.0000")


async def test_transfer_from_someone_elses_account_forbidden(client: httpx.AsyncClient) -> None:
    _, alice = await register_and_login(client, email="alice3@example.com")
    _, bob = await register_and_login(client, email="bob3@example.com")
    a = await _create_account(client, alice)
    b = await _create_account(client, bob)
    await _deposit(client, alice, a, "100.00")

    r = await client.post(
        "/transactions/transfer",
        headers=auth_headers(bob, idempotency_key=str(uuid.uuid4())),
        json={"from_account_id": a, "to_account_id": b, "amount": "1.00"},
    )
    assert r.status_code == 403


async def test_same_account_transfer_rejected(client: httpx.AsyncClient) -> None:
    _, token = await register_and_login(client)
    acct = await _create_account(client, token)
    await _deposit(client, token, acct, "10.00")

    r = await client.post(
        "/transactions/transfer",
        headers=auth_headers(token, idempotency_key=str(uuid.uuid4())),
        json={"from_account_id": acct, "to_account_id": acct, "amount": "1.00"},
    )
    assert r.status_code == 422
    assert r.json()["error"] == "same_account_transfer"


async def test_cross_currency_transfer_rejected(client: httpx.AsyncClient) -> None:
    _, token = await register_and_login(client)
    usd = await _create_account(client, token, currency="USD")
    eur = await _create_account(client, token, currency="EUR")
    await _deposit(client, token, usd, "50.00")

    r = await client.post(
        "/transactions/transfer",
        headers=auth_headers(token, idempotency_key=str(uuid.uuid4())),
        json={"from_account_id": usd, "to_account_id": eur, "amount": "5.00"},
    )
    assert r.status_code == 422
    assert r.json()["error"] == "currency_mismatch"


async def test_raw_ledger_invariant_per_transfer(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    """Exercises every transfer kind, then asserts directly against the
    ledger_entries table that SUM(DEBIT) == SUM(CREDIT) for each transfer."""
    _, alice = await register_and_login(client, email="inv-alice@example.com")
    _, bob = await register_and_login(client, email="inv-bob@example.com")
    a = await _create_account(client, alice)
    b = await _create_account(client, bob)
    await _deposit(client, alice, a, "500.00")
    await client.post(
        "/transactions/withdraw",
        headers=auth_headers(alice, idempotency_key=str(uuid.uuid4())),
        json={"account_id": a, "amount": "50.00"},
    )
    await client.post(
        "/transactions/transfer",
        headers=auth_headers(alice, idempotency_key=str(uuid.uuid4())),
        json={"from_account_id": a, "to_account_id": b, "amount": "123.45"},
    )

    from app.models.transaction import EntryType, LedgerEntry

    entries = (await db.scalars(select(LedgerEntry))).all()
    assert entries

    by_transfer: dict = {}
    for e in entries:
        by_transfer.setdefault(e.transfer_id, {"DEBIT": Decimal("0"), "CREDIT": Decimal("0")})
        if e.entry_type == EntryType.DEBIT:
            by_transfer[e.transfer_id]["DEBIT"] += e.amount
        else:
            by_transfer[e.transfer_id]["CREDIT"] += e.amount

    for tid, sums in by_transfer.items():
        assert sums["DEBIT"] == sums["CREDIT"], f"unbalanced transfer {tid}: {sums}"
