"""Reusable audit logging for every module that mutates state or handles a
security-relevant event.

Usage pattern for future modules (e.g. restaurant CRUD, review queue):

    audit = AuditService(db)
    await audit.log(
        action=AuditAction.RESTAURANT_EDIT,
        entity_type=AuditEntityType.RESTAURANT,
        entity_id=str(restaurant.id),
        actor=current_user,
        old_values={"name": old_name},
        new_values={"name": new_name},
    )

`AuditService` only ever `flush()`es (never commits) — it participates in
the caller's existing transaction, so the audit row and the business
change it describes are atomic: either both land or neither does. Callers
already `await db.commit()` once at the end of the request, exactly as the
auth router does today.

Deliberately does NOT swallow errors: an audit call that fails should fail
the request loudly (and roll back the mutation with it) rather than let an
unauditable change through silently. If a future caller genuinely needs
"best effort" logging (e.g. logging a *failed* login, where the login
itself has nothing to roll back), it should catch around the audit call
itself, not have AuditService swallow it generically.
"""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from core.schemas.audit import AuditAction, AuditEntityType
from database.models.audit_log import AuditLog
from database.models.user import User
from database.repositories.audit_log_repository import AuditLogRepository


class AuditService:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = AuditLogRepository(session)

    async def log(
        self,
        *,
        action: AuditAction,
        entity_type: AuditEntityType,
        entity_id: str,
        actor: User | None = None,
        actor_email: str | None = None,
        old_values: dict[str, Any] | None = None,
        new_values: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        agent_run_id: str | None = None,
    ) -> AuditLog:
        """Writes one audit row.

        `actor` is the usual path (an authenticated User) — pass
        `actor_email` instead for events with no resolvable account (e.g. a
        failed login against an email that doesn't exist), so the log still
        records *who was claimed* even without an actor_id to point at.
        """
        return await self._repo.create(
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            actor_id=actor.id if actor is not None else None,
            actor_email=actor_email if actor_email is not None else (actor.email if actor else None),
            old_values=old_values,
            new_values=new_values,
            metadata=metadata,
            agent_run_id=agent_run_id,
        )

    async def log_security_event(
        self,
        *,
        action: AuditAction,
        actor: User | None = None,
        actor_email: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AuditLog:
        """Convenience wrapper for login/logout/session events — these are
        entity_type=SESSION keyed by the actor's id (falling back to a
        random id for events with no account, e.g. an unknown-email login
        attempt, so entity_id is never empty)."""
        entity_id = str(actor.id) if actor is not None else (actor_email or str(uuid.uuid4()))
        return await self.log(
            action=action,
            entity_type=AuditEntityType.SESSION,
            entity_id=entity_id,
            actor=actor,
            actor_email=actor_email,
            metadata=metadata,
        )
