"""Integration tests for the login / refresh / logout HTTP flow, run
against a real FastAPI app + Postgres transaction (see tests/conftest.py)."""

import pytest
from httpx import AsyncClient

from database.models.user import User
from tests.conftest import auth_headers, login

pytestmark = pytest.mark.asyncio


class TestLogin:
    async def test_login_with_correct_credentials_returns_tokens(
        self, app_client: AsyncClient, viewer_user: User, user_password: str
    ) -> None:
        response = await app_client.post(
            "/api/v1/auth/login",
            json={"email": viewer_user.email, "password": user_password},
        )
        assert response.status_code == 200
        body = response.json()
        assert "access_token" in body
        assert "refresh_token" in body
        assert body["token_type"] == "bearer"

    async def test_login_with_wrong_password_returns_401(
        self, app_client: AsyncClient, viewer_user: User
    ) -> None:
        response = await app_client.post(
            "/api/v1/auth/login",
            json={"email": viewer_user.email, "password": "wrong-password"},
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthorized"

    async def test_login_with_unknown_email_returns_401(self, app_client: AsyncClient) -> None:
        response = await app_client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@hungrx.example", "password": "irrelevant"},
        )
        assert response.status_code == 401

    async def test_login_for_inactive_user_returns_401(
        self, app_client: AsyncClient, inactive_user: User, user_password: str
    ) -> None:
        response = await app_client.post(
            "/api/v1/auth/login",
            json={"email": inactive_user.email, "password": user_password},
        )
        assert response.status_code == 401

    async def test_login_response_never_contains_password_hash(
        self, app_client: AsyncClient, viewer_user: User, user_password: str
    ) -> None:
        response = await app_client.post(
            "/api/v1/auth/login",
            json={"email": viewer_user.email, "password": user_password},
        )
        assert "hashed_password" not in response.text
        assert "password" not in response.json()


class TestMe:
    async def test_me_returns_current_user_with_valid_token(
        self, app_client: AsyncClient, viewer_user: User, user_password: str
    ) -> None:
        tokens = await login(app_client, email=viewer_user.email, password=user_password)
        response = await app_client.get("/api/v1/auth/me", headers=auth_headers(tokens["access_token"]))
        assert response.status_code == 200
        body = response.json()
        assert body["email"] == viewer_user.email
        assert body["role"] == "VIEWER"
        assert "hashed_password" not in body

    async def test_me_without_token_returns_401(self, app_client: AsyncClient) -> None:
        response = await app_client.get("/api/v1/auth/me")
        assert response.status_code == 401

    async def test_me_with_garbage_token_returns_401(self, app_client: AsyncClient) -> None:
        response = await app_client.get(
            "/api/v1/auth/me", headers=auth_headers("not-a-real-jwt")
        )
        assert response.status_code == 401

    async def test_me_with_deactivated_account_returns_401_even_with_valid_token(
        self, app_client: AsyncClient, db_session, inactive_user: User, user_password: str
    ) -> None:
        # Reactivate momentarily to obtain a legitimate token, then deactivate
        # again — proves the check is against live DB state, not the token.
        inactive_user.is_active = True
        await db_session.commit()
        tokens = await login(app_client, email=inactive_user.email, password=user_password)

        inactive_user.is_active = False
        await db_session.commit()

        response = await app_client.get(
            "/api/v1/auth/me", headers=auth_headers(tokens["access_token"])
        )
        assert response.status_code == 401


class TestRefresh:
    async def test_refresh_with_valid_token_returns_new_token_pair(
        self, app_client: AsyncClient, viewer_user: User, user_password: str
    ) -> None:
        tokens = await login(app_client, email=viewer_user.email, password=user_password)
        response = await app_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert response.status_code == 200
        new_tokens = response.json()
        assert new_tokens["access_token"] != tokens["access_token"]
        assert new_tokens["refresh_token"] != tokens["refresh_token"]

    async def test_refresh_token_is_single_use(
        self, app_client: AsyncClient, viewer_user: User, user_password: str
    ) -> None:
        tokens = await login(app_client, email=viewer_user.email, password=user_password)
        first = await app_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert first.status_code == 200

        second = await app_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert second.status_code == 401

    async def test_refresh_with_access_token_is_rejected(
        self, app_client: AsyncClient, viewer_user: User, user_password: str
    ) -> None:
        tokens = await login(app_client, email=viewer_user.email, password=user_password)
        response = await app_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["access_token"]}
        )
        assert response.status_code == 401

    async def test_refresh_with_garbage_token_returns_401(self, app_client: AsyncClient) -> None:
        response = await app_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": "not-a-real-jwt"}
        )
        assert response.status_code == 401


class TestLogout:
    async def test_logout_revokes_refresh_token(
        self, app_client: AsyncClient, viewer_user: User, user_password: str
    ) -> None:
        tokens = await login(app_client, email=viewer_user.email, password=user_password)

        logout_response = await app_client.post(
            "/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]}
        )
        assert logout_response.status_code == 204

        refresh_response = await app_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert refresh_response.status_code == 401

    async def test_logout_all_revokes_every_session(
        self, app_client: AsyncClient, viewer_user: User, user_password: str
    ) -> None:
        session_a = await login(app_client, email=viewer_user.email, password=user_password)
        session_b = await login(app_client, email=viewer_user.email, password=user_password)

        response = await app_client.post(
            "/api/v1/auth/logout-all", headers=auth_headers(session_a["access_token"])
        )
        assert response.status_code == 204

        for tokens in (session_a, session_b):
            refresh_response = await app_client.post(
                "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
            )
            assert refresh_response.status_code == 401

    async def test_logout_all_requires_authentication(self, app_client: AsyncClient) -> None:
        response = await app_client.post("/api/v1/auth/logout-all")
        assert response.status_code == 401
