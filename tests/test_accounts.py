import httpx

from tests.conftest import auth_headers, register_and_login


async def test_create_list_get_account(client: httpx.AsyncClient) -> None:
    _, token = await register_and_login(client)

    r = await client.post(
        "/accounts",
        headers=auth_headers(token),
        json={"currency": "USD", "name": "Main"},
    )
    assert r.status_code == 201
    acct = r.json()
    assert acct["currency"] == "USD"
    assert acct["balance"] == "0.0000"

    r = await client.get("/accounts", headers=auth_headers(token))
    assert r.status_code == 200
    assert len(r.json()) == 1

    r = await client.get(f"/accounts/{acct['id']}", headers=auth_headers(token))
    assert r.status_code == 200
    assert r.json()["id"] == acct["id"]


async def test_cannot_see_other_users_account(client: httpx.AsyncClient) -> None:
    _, alice_token = await register_and_login(client, email="alice@example.com")
    _, bob_token = await register_and_login(client, email="bob@example.com")

    r = await client.post(
        "/accounts",
        headers=auth_headers(alice_token),
        json={"currency": "USD"},
    )
    alice_acct_id = r.json()["id"]

    r = await client.get(f"/accounts/{alice_acct_id}", headers=auth_headers(bob_token))
    assert r.status_code == 403
    assert r.json()["error"] == "account_forbidden"


async def test_get_unknown_account_returns_404(client: httpx.AsyncClient) -> None:
    _, token = await register_and_login(client)
    r = await client.get(
        "/accounts/00000000-0000-0000-0000-000000000000",
        headers=auth_headers(token),
    )
    assert r.status_code == 404
