"""Tests for /api/auth: registration, login (with session eviction), logout, me."""
from __future__ import annotations

import secrets
from typing import Any

import pytest


def _random_username() -> str:
    return "user_" + secrets.token_hex(4)


def test_register_returns_201_and_user(client: Any) -> None:
    resp = client.post(
        "/api/auth/register",
        json={"username": "newuser1", "password": "password123"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["username"] == "newuser1"
    assert isinstance(body["id"], int)
    assert "created_at" in body
    # Never leak the hash.
    assert "password" not in body
    assert "password_hash" not in body


def test_register_duplicate_returns_409(client: Any) -> None:
    payload = {"username": "dupe_user", "password": "password123"}
    first = client.post("/api/auth/register", json=payload)
    assert first.status_code == 201
    second = client.post("/api/auth/register", json=payload)
    assert second.status_code == 409
    assert "detail" in second.json()


@pytest.mark.parametrize("bad_password", ["", "short", "1234567"])
def test_register_rejects_short_password_400_or_422(client: Any, bad_password: str) -> None:
    resp = client.post(
        "/api/auth/register",
        json={"username": "shorty", "password": bad_password},
    )
    assert resp.status_code in (400, 422), resp.text


@pytest.mark.parametrize(
    "bad_username",
    ["ab", "имя", "user name", "user@example", "!!!", "a" * 65],
)
def test_register_rejects_invalid_username_pattern(client: Any, bad_username: str) -> None:
    resp = client.post(
        "/api/auth/register",
        json={"username": bad_username, "password": "password123"},
    )
    assert resp.status_code in (400, 422), resp.text


def test_login_returns_token(client: Any) -> None:
    client.post(
        "/api/auth/register",
        json={"username": "loginer", "password": "password123"},
    )
    resp = client.post(
        "/api/auth/login",
        json={"username": "loginer", "password": "password123"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert isinstance(body["token"], str) and len(body["token"]) >= 16


def test_login_wrong_password_returns_401(client: Any) -> None:
    client.post(
        "/api/auth/register",
        json={"username": "wrongpw", "password": "password123"},
    )
    resp = client.post(
        "/api/auth/login",
        json={"username": "wrongpw", "password": "different1"},
    )
    assert resp.status_code == 401


def test_login_unknown_user_returns_401(client: Any) -> None:
    resp = client.post(
        "/api/auth/login",
        json={"username": "ghost_user", "password": "password123"},
    )
    assert resp.status_code == 401


def test_login_evicts_all_other_sessions(client: Any) -> None:
    # User A registers and logs in.
    client.post(
        "/api/auth/register",
        json={"username": "alice", "password": "password123"},
    )
    token_a = client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "password123"},
    ).json()["token"]

    # A can hit /me.
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token_a}"})
    assert me.status_code == 200
    assert me.json()["username"] == "alice"

    # User B registers and logs in — this deletes every other session including A's.
    client.post(
        "/api/auth/register",
        json={"username": "bob", "password": "password456"},
    )
    token_b = client.post(
        "/api/auth/login",
        json={"username": "bob", "password": "password456"},
    ).json()["token"]

    # A's token is now invalid.
    me_a = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token_a}"})
    assert me_a.status_code == 401

    # B's token still works.
    me_b = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token_b}"})
    assert me_b.status_code == 200
    assert me_b.json()["username"] == "bob"


def test_logout_deletes_session(authed_client: tuple[Any, str, int]) -> None:
    client, token, _ = authed_client
    resp = client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 204

    # Same token → 401 afterwards.
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 401


def test_me_returns_user(authed_client: tuple[Any, str, int]) -> None:
    client, token, user_id = authed_client
    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == user_id
    assert body["username"].startswith("alice_")


@pytest.mark.parametrize(
    "headers",
    [
        {},  # missing header
        {"Authorization": ""},  # empty header
        {"Authorization": "Bearer"},  # no token
        {"Authorization": "Bearer "},  # empty token after space
        {"Authorization": "Basic dXNlcjpwYXNz"},  # wrong scheme
        {"Authorization": "Bearer not_a_real_token"},  # unknown token
        {"Authorization": "not-even-a-scheme"},  # malformed
    ],
)
def test_missing_auth_returns_401(client: Any, headers: dict[str, str]) -> None:
    resp = client.get("/api/auth/me", headers=headers)
    assert resp.status_code in (401, 403), (
        f"expected 401/403 for headers {headers!r}, got {resp.status_code}: {resp.text}"
    )
