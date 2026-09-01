import uuid
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.schemas.audit import AuditAction, AuditEntityType
from database.models.audit_log import AuditLog


class AuditLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        action: AuditAction,
        entity_type: AuditEntityType,
        entity_id: str,
        actor_id: uuid.UUID | None,
        actor_email: str | None,
        old_values: dict[str, Any] | None = None,
        new_values: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        agent_run_id: str | None = None,
    ) -> AuditLog:
        record = AuditLog(
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            actor_id=actor_id,
            actor_email=actor_email,
            old_values=old_values,
            new_values=new_values,
            metadata_=metadata,
            agent_run_id=agent_run_id,
        )
        self._session.add(record)
        await self._session.flush()
        return record

    async def list_for_entity(
        self, *, entity_type: AuditEntityType, entity_id: str, limit: int = 100
    ) -> list[AuditLog]:
        result = await self._session.execute(
            select(AuditLog)
            .where(AuditLog.entity_type == entity_type, AuditLog.entity_id == entity_id)
            .order_by(desc(AuditLog.created_at))
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_recent(self, *, limit: int = 100) -> list[AuditLog]:
        result = await self._session.execute(
            select(AuditLog).order_by(desc(AuditLog.created_at)).limit(limit)
        )
        return list(result.scalars().all())
