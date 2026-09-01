"""Integration tests proving the security/auth flows actually write audit
rows through the real HTTP layer, and that mutating admin endpoints do the
same — run against a real FastAPI app + Postgres transaction (see
tests/conftest.py)."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from core.schemas.audit import AuditAction, AuditEntityType
from database.models.audit_log import AuditLog
from database.models.user import User
from tests.conftest import auth_headers, login

pytestmark = pytest.mark.asyncio


async def _actions_for(db_session, entity_type: AuditEntityType, entity_id: str) -> list[AuditAction]:
    result = await db_session.execute(
        select(AuditLog).where(AuditLog.entity_type == entity_type, AuditLog.entity_id == entity_id)
    )
    return [row.action for row in result.scalars().all()]


class TestLoginAudit:
    async def test_successful_login_writes_login_success_row(
        self, app_client: AsyncClient, db_session, viewer_user: User, user_password: str
    ) -> None:
        await app_client.post(
            "/api/v1/auth/login", json={"email": viewer_user.email, "password": user_password}
        )

        result = await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == AuditAction.LOGIN_SUCCESS,
                AuditLog.actor_id == viewer_user.id,
            )
        )
        entry = result.scalar_one()
        assert entry.actor_email == viewer_user.email
        assert entry.entity_type == AuditEntityType.SESSION
        assert entry.entity_id == str(viewer_user.id)

    async def test_failed_login_writes_login_failure_row_with_no_actor_id(
        self, app_client: AsyncClient, db_session, viewer_user: User
    ) -> None:
        await app_client.post(
            "/api/v1/auth/login",
            json={"email": viewer_user.email, "password": "wrong-password"},
        )

        result = await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == AuditAction.LOGIN_FAILURE,
                AuditLog.actor_email == viewer_user.email,
            )
        )
        entry = result.scalar_one()
        assert entry.actor_id is None

    async def test_failed_login_for_unknown_email_writes_audit_row(
        self, app_client: AsyncClient, db_session
    ) -> None:
        await app_client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@hungrx.example", "password": "irrelevant"},
        )

        result = await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == AuditAction.LOGIN_FAILURE,
                AuditLog.actor_email == "nobody@hungrx.example",
            )
        )
        entry = result.scalar_one()
        assert entry.actor_id is None


class TestSessionAudit:
    async def test_logout_writes_logout_row_attributed_to_owner(
        self, app_client: AsyncClient, db_session, viewer_user: User, user_password: str
    ) -> None:
        tokens = await login(app_client, email=viewer_user.email, password=user_password)
        await app_client.post("/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]})

        result = await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == AuditAction.LOGOUT, AuditLog.actor_id == viewer_user.id
            )
        )
        assert result.scalar_one() is not None

    async def test_logout_all_writes_logout_all_row(
        self, app_client: AsyncClient, db_session, viewer_user: User, user_password: str
    ) -> None:
        tokens = await login(app_client, email=viewer_user.email, password=user_password)
        await app_client.post(
            "/api/v1/auth/logout-all", headers=auth_headers(tokens["access_token"])
        )

        result = await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == AuditAction.LOGOUT_ALL, AuditLog.actor_id == viewer_user.id
            )
        )
        assert result.scalar_one() is not None

    async def test_refresh_writes_token_refresh_row(
        self, app_client: AsyncClient, db_session, viewer_user: User, user_password: str
    ) -> None:
        tokens = await login(app_client, email=viewer_user.email, password=user_password)
        await app_client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})

        result = await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == AuditAction.TOKEN_REFRESH, AuditLog.actor_id == viewer_user.id
            )
        )
        assert result.scalar_one() is not None


class TestAdminMutationAudit:
    async def test_create_restaurant_placeholder_writes_audit_row(
        self, app_client: AsyncClient, db_session, data_manager_user: User, user_password: str
    ) -> None:
        tokens = await login(app_client, email=data_manager_user.email, password=user_password)
        response = await app_client.post(
            "/api/v1/admin/restaurants", headers=auth_headers(tokens["access_token"])
        )
        assert response.status_code == 200
        audit_log_id = response.json()["audit_log_id"]

        result = await db_session.execute(select(AuditLog).where(AuditLog.id == audit_log_id))
        entry = result.scalar_one()
        assert entry.action == AuditAction.RESTAURANT_CREATE
        assert entry.actor_id == data_manager_user.id

    async def test_confirm_review_item_writes_audit_row(
        self, app_client: AsyncClient, db_session, reviewer_user: User, user_password: str
    ) -> None:
        tokens = await login(app_client, email=reviewer_user.email, password=user_password)
        response = await app_client.post(
            "/api/v1/admin/review/item-42/confirm", headers=auth_headers(tokens["access_token"])
        )
        assert response.status_code == 200

        actions = await _actions_for(db_session, AuditEntityType.PROPOSED_CHANGE, "item-42")
        assert AuditAction.PROPOSED_CHANGE_APPROVE in actions

    async def test_trigger_ingestion_writes_audit_row(
        self, app_client: AsyncClient, db_session, data_manager_user: User, user_password: str
    ) -> None:
        tokens = await login(app_client, email=data_manager_user.email, password=user_password)
        response = await app_client.post(
            "/api/v1/admin/ingestion/trigger", headers=auth_headers(tokens["access_token"])
        )
        assert response.status_code == 200

        actions = await _actions_for(db_session, AuditEntityType.AGENT_RUN, "placeholder")
        assert AuditAction.AGENT_RUN_TRIGGER in actions

    async def test_audit_log_endpoint_returns_recent_entries_for_viewer(
        self,
        app_client: AsyncClient,
        db_session,
        viewer_user: User,
        data_manager_user: User,
        user_password: str,
    ) -> None:
        dm_tokens = await login(app_client, email=data_manager_user.email, password=user_password)
        await app_client.post(
            "/api/v1/admin/restaurants", headers=auth_headers(dm_tokens["access_token"])
        )

        viewer_tokens = await login(app_client, email=viewer_user.email, password=user_password)
        response = await app_client.get(
            "/api/v1/admin/audit-log", headers=auth_headers(viewer_tokens["access_token"])
        )
        assert response.status_code == 200
        body = response.json()
        assert len(body) >= 1
        assert any(entry["action"] == AuditAction.RESTAURANT_CREATE.value for entry in body)

    async def test_audit_log_endpoint_requires_permission(
        self, app_client: AsyncClient, reviewer_user: User, user_password: str
    ) -> None:
        # REVIEWER does not hold AUDIT_LOG_READ in the permission matrix.
        tokens = await login(app_client, email=reviewer_user.email, password=user_password)
        response = await app_client.get(
            "/api/v1/admin/audit-log", headers=auth_headers(tokens["access_token"])
        )
        assert response.status_code == 403
