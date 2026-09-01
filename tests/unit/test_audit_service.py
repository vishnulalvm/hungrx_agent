"""Unit tests for AuditService/AuditLogRepository against a real Postgres
transaction (see tests/conftest.py) — no HTTP layer involved."""

import pytest

from apps.api.app.services.audit_service import AuditService
from core.schemas.audit import AuditAction, AuditEntityType
from database.models.user import User
from database.repositories.audit_log_repository import AuditLogRepository

pytestmark = pytest.mark.asyncio


class TestAuditServiceLog:
    async def test_log_persists_actor_and_values(self, db_session, viewer_user: User) -> None:
        audit = AuditService(db_session)
        entry = await audit.log(
            action=AuditAction.RESTAURANT_EDIT,
            entity_type=AuditEntityType.RESTAURANT,
            entity_id="restaurant-123",
            actor=viewer_user,
            old_values={"name": "Old Name"},
            new_values={"name": "New Name"},
        )

        assert entry.id is not None
        assert entry.actor_id == viewer_user.id
        assert entry.actor_email == viewer_user.email
        assert entry.old_values == {"name": "Old Name"}
        assert entry.new_values == {"name": "New Name"}
        assert entry.created_at is not None

    async def test_log_with_no_actor_stores_null_actor_id(self, db_session) -> None:
        audit = AuditService(db_session)
        entry = await audit.log(
            action=AuditAction.LOGIN_FAILURE,
            entity_type=AuditEntityType.SESSION,
            entity_id="unknown@hungrx.example",
            actor_email="unknown@hungrx.example",
        )

        assert entry.actor_id is None
        assert entry.actor_email == "unknown@hungrx.example"

    async def test_log_captures_agent_run_id(self, db_session, viewer_user: User) -> None:
        audit = AuditService(db_session)
        entry = await audit.log(
            action=AuditAction.AI_EXTRACTION,
            entity_type=AuditEntityType.RESTAURANT,
            entity_id="restaurant-123",
            actor=viewer_user,
            agent_run_id="run-abc-123",
        )
        assert entry.agent_run_id == "run-abc-123"

    async def test_log_actor_email_defaults_to_actor_email_when_not_overridden(
        self, db_session, viewer_user: User
    ) -> None:
        audit = AuditService(db_session)
        entry = await audit.log(
            action=AuditAction.RESTAURANT_CREATE,
            entity_type=AuditEntityType.RESTAURANT,
            entity_id="restaurant-999",
            actor=viewer_user,
        )
        assert entry.actor_email == viewer_user.email


class TestAuditServiceLogSecurityEvent:
    async def test_login_success_keyed_by_actor_id(self, db_session, viewer_user: User) -> None:
        audit = AuditService(db_session)
        entry = await audit.log_security_event(action=AuditAction.LOGIN_SUCCESS, actor=viewer_user)

        assert entry.entity_type == AuditEntityType.SESSION
        assert entry.entity_id == str(viewer_user.id)
        assert entry.actor_id == viewer_user.id

    async def test_login_failure_with_unknown_email_has_no_actor_id(self, db_session) -> None:
        audit = AuditService(db_session)
        entry = await audit.log_security_event(
            action=AuditAction.LOGIN_FAILURE, actor_email="ghost@hungrx.example"
        )

        assert entry.actor_id is None
        assert entry.actor_email == "ghost@hungrx.example"
        assert entry.entity_id == "ghost@hungrx.example"


class TestAuditLogRepository:
    async def test_list_for_entity_returns_only_matching_rows(
        self, db_session, viewer_user: User
    ) -> None:
        audit = AuditService(db_session)
        await audit.log(
            action=AuditAction.RESTAURANT_CREATE,
            entity_type=AuditEntityType.RESTAURANT,
            entity_id="r-1",
            actor=viewer_user,
        )
        await audit.log(
            action=AuditAction.RESTAURANT_EDIT,
            entity_type=AuditEntityType.RESTAURANT,
            entity_id="r-1",
            actor=viewer_user,
        )
        await audit.log(
            action=AuditAction.RESTAURANT_CREATE,
            entity_type=AuditEntityType.RESTAURANT,
            entity_id="r-2",
            actor=viewer_user,
        )

        repo = AuditLogRepository(db_session)
        entries = await repo.list_for_entity(entity_type=AuditEntityType.RESTAURANT, entity_id="r-1")

        # created_at has only microsecond precision and these three inserts
        # can land in the same instant, so strict newest-first ordering
        # isn't guaranteed without a monotonic tiebreaker column — assert
        # the correct rows come back, not a specific order between them.
        assert len(entries) == 2
        assert {entry.action for entry in entries} == {
            AuditAction.RESTAURANT_CREATE,
            AuditAction.RESTAURANT_EDIT,
        }

    async def test_list_recent_respects_limit(self, db_session, viewer_user: User) -> None:
        audit = AuditService(db_session)
        for i in range(5):
            await audit.log(
                action=AuditAction.RESTAURANT_CREATE,
                entity_type=AuditEntityType.RESTAURANT,
                entity_id=f"r-{i}",
                actor=viewer_user,
            )

        repo = AuditLogRepository(db_session)
        entries = await repo.list_recent(limit=3)
        assert len(entries) == 3
