"""Cross-currency transfer, account freeze, and admin-driven reversal. These
exercise the 4-leg FX posting path and prove the per-currency invariant still
holds."""
from __future__ import annotations

import os
import uuid
from decimal import Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import auth_headers, register_and_login


async def _create_account(client: httpx.AsyncClient, token: str, currency: str) -> str:
    r = await client.post("/accounts", headers=auth_headers(token), json={"currency": currency})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _deposit(client: httpx.AsyncClient, token: str, account_id: str, amount: str) -> None:
    r = await client.post(
        "/transactions/deposit",
        headers=auth_headers(token, idempotency_key=str(uuid.uuid4())),
        json={"account_id": account_id, "amount": amount},
    )
    assert r.status_code == 201, r.text


async def test_cross_currency_transfer_balances_per_currency(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    # admin first, so we can upsert an FX rate
    admin_email = "admin@ledger.local"
    os.environ["ADMIN_EMAILS"] = admin_email
    from app.config import get_settings
    get_settings.cache_clear()

    _, admin_token = await register_and_login(client, email=admin_email)

    r = await client.post(
        "/admin/fx-rates",
        headers=auth_headers(admin_token),
        json={"from_currency": "USD", "to_currency": "EUR", "rate": "0.9000"},
    )
    assert r.status_code == 201, r.text

    _, alice = await register_and_login(client, email="fx-alice@example.com")
    _, bob = await register_and_login(client, email="fx-bob@example.com")
    usd = await _create_account(client, alice, "USD")
    eur = await _create_account(client, bob, "EUR")
    await _deposit(client, alice, usd, "100.00")

    r = await client.post(
        "/transactions/transfer",
        headers=auth_headers(alice, idempotency_key=str(uuid.uuid4())),
        json={"from_account_id": usd, "to_account_id": eur, "amount": "50.00"},
    )
    assert r.status_code == 201, r.text
    transfer = r.json()
    # 4 legs: debit alice USD, credit system USD, debit system EUR, credit bob EUR
    assert len(transfer["entries"]) == 4

    # balances: alice 50 USD, bob 45 EUR (50 * 0.9)
    assert Decimal((await client.get(f"/accounts/{usd}", headers=auth_headers(alice))).json()["balance"]) == Decimal("50.0000")
    assert Decimal((await client.get(f"/accounts/{eur}", headers=auth_headers(bob))).json()["balance"]) == Decimal("45.0000")

    # per-currency balance invariant directly from the ledger
    from app.models.transaction import EntryType, LedgerEntry

    entries = list((await db.scalars(select(LedgerEntry))).all())
    per_currency: dict = {}
    for e in entries:
        per_currency.setdefault(e.currency, {"DEBIT": Decimal("0"), "CREDIT": Decimal("0")})
        if e.entry_type == EntryType.DEBIT:
            per_currency[e.currency]["DEBIT"] += e.amount
        else:
            per_currency[e.currency]["CREDIT"] += e.amount
    for currency, sums in per_currency.items():
        assert sums["DEBIT"] == sums["CREDIT"], f"unbalanced {currency}: {sums}"


async def test_frozen_account_cannot_debit(client: httpx.AsyncClient) -> None:
    admin_email = "admin2@ledger.local"
    os.environ["ADMIN_EMAILS"] = admin_email
    from app.config import get_settings
    get_settings.cache_clear()

    _, admin_token = await register_and_login(client, email=admin_email)
    _, alice = await register_and_login(client, email="frozen@example.com")
    acct = await _create_account(client, alice, "USD")
    await _deposit(client, alice, acct, "100.00")

    r = await client.post(
        f"/admin/accounts/{acct}/status",
        headers=auth_headers(admin_token),
        json={"status": "FROZEN"},
    )
    assert r.status_code == 200

    r = await client.post(
        "/transactions/withdraw",
        headers=auth_headers(alice, idempotency_key=str(uuid.uuid4())),
        json={"account_id": acct, "amount": "10.00"},
    )
    assert r.status_code == 423
    assert r.json()["error"] == "account_frozen"


async def test_admin_can_reverse_transfer(client: httpx.AsyncClient) -> None:
    admin_email = "admin3@ledger.local"
    os.environ["ADMIN_EMAILS"] = admin_email
    from app.config import get_settings
    get_settings.cache_clear()

    _, admin_token = await register_and_login(client, email=admin_email)
    _, alice = await register_and_login(client, email="rev-a@example.com")
    _, bob = await register_and_login(client, email="rev-b@example.com")
    a = await _create_account(client, alice, "USD")
    b = await _create_account(client, bob, "USD")
    await _deposit(client, alice, a, "100.00")

    r = await client.post(
        "/transactions/transfer",
        headers=auth_headers(alice, idempotency_key=str(uuid.uuid4())),
        json={"from_account_id": a, "to_account_id": b, "amount": "25.00"},
    )
    tid = r.json()["id"]

    r = await client.post(
        f"/admin/transfers/{tid}/reverse",
        headers=auth_headers(admin_token),
        json={"reason": "fraud"},
    )
    assert r.status_code == 201, r.text

    assert Decimal((await client.get(f"/accounts/{a}", headers=auth_headers(alice))).json()["balance"]) == Decimal("100.0000")
    assert Decimal((await client.get(f"/accounts/{b}", headers=auth_headers(bob))).json()["balance"]) == Decimal("0.0000")
