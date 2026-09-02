"""Integration coverage for apps/api/app/core/rate_limit.py's login/
refresh throttling — part of the security review's fix for unlimited
brute-force login attempts. Exercised through the real HTTP layer with
`get_settings` overridden to a non-"test" environment (rate_limit_login/
rate_limit_refresh are deliberately no-ops when environment=="test", the
value the rest of this test suite runs with — see
tests/conftest.py — so this file has to opt back into real throttling to
prove it works at all) and redis_url pointed at DB 15, separate from the
dev/prod default DB 0, matching tests/unit/test_job_lock.py's pattern.
"""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
import redis.asyncio as aioredis
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.security import hash_password
from core.config.settings import Settings
from core.schemas.auth import Role
from database.models.user import User

pytestmark = pytest.mark.asyncio

_TEST_REDIS_URL = "redis://redis:6379/15"


@pytest_asyncio.fixture
async def rate_limited_client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    from apps.api.app.dependencies.db import get_db_session
    from apps.api.app.dependencies.settings import get_settings as get_settings_dep
    from apps.api.app.main import app
    from core.config.settings import get_settings as get_settings_module

    async def _override_get_db_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    test_settings = Settings(environment="development", redis_url=_TEST_REDIS_URL)
    app.dependency_overrides[get_db_session] = _override_get_db_session
    app.dependency_overrides[get_settings_dep] = lambda: test_settings
    app.dependency_overrides[get_settings_module] = lambda: test_settings

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client

    app.dependency_overrides.clear()

    conn = aioredis.from_url(_TEST_REDIS_URL)
    await conn.flushdb()
    await conn.aclose()


class TestLoginRateLimit:
    async def test_sixth_attempt_for_the_same_email_within_a_minute_is_throttled(
        self, rate_limited_client: AsyncClient
    ) -> None:
        payload = {"email": "throttle-target@hungrx.example", "password": "wrong-password"}

        for _ in range(5):
            response = await rate_limited_client.post("/api/v1/auth/login", json=payload)
            assert response.status_code == 401

        response = await rate_limited_client.post("/api/v1/auth/login", json=payload)
        assert response.status_code == 429
        assert response.json()["error"]["code"] == "rate_limited"

    async def test_a_different_email_is_not_throttled_by_another_identifiers_attempts(
        self, rate_limited_client: AsyncClient
    ) -> None:
        exhausted = {"email": "exhausted@hungrx.example", "password": "wrong-password"}
        for _ in range(5):
            await rate_limited_client.post("/api/v1/auth/login", json=exhausted)
        # The 6th call for `exhausted` would 429 (proven above) — a
        # different email must still get a normal 401, not 429.
        response = await rate_limited_client.post(
            "/api/v1/auth/login", json={"email": "unrelated@hungrx.example", "password": "wrong-password"}
        )
        assert response.status_code == 401

    async def test_legitimate_login_still_succeeds_under_the_limit(
        self, rate_limited_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = User(
            email="throttle-real-user@hungrx.example",
            full_name="Real User",
            role=Role.VIEWER,
            is_active=True,
            hashed_password=hash_password("CorrectPass123!"),
        )
        db_session.add(user)
        await db_session.flush()

        response = await rate_limited_client.post(
            "/api/v1/auth/login",
            json={"email": "throttle-real-user@hungrx.example", "password": "CorrectPass123!"},
        )
        assert response.status_code == 200
        assert "access_token" in response.json()


class TestRefreshRateLimit:
    async def test_refresh_is_throttled_after_the_per_ip_limit(
        self, rate_limited_client: AsyncClient
    ) -> None:
        for _ in range(20):
            response = await rate_limited_client.post(
                "/api/v1/auth/refresh", json={"refresh_token": "not-a-real-token"}
            )
            assert response.status_code == 401

        response = await rate_limited_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": "not-a-real-token"}
        )
        assert response.status_code == 429
