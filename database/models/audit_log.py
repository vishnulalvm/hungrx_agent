import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.schemas.audit import AuditAction, AuditEntityType
from database.models.base import Base


class AuditLog(Base):
    """Append-only record of every auditable operation.

    Deliberately has no update/delete path anywhere in the codebase — rows
    are written once by AuditService and never mutated, so the log stays a
    trustworthy record even if the actor was later demoted or deleted
    (actor_id is nullable + ON DELETE SET NULL, so a removed user's history
    survives them).
    """

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    action: Mapped[AuditAction] = mapped_column(
        Enum(AuditAction, name="audit_action", native_enum=True), nullable=False, index=True
    )
    entity_type: Mapped[AuditEntityType] = mapped_column(
        Enum(AuditEntityType, name="audit_entity_type", native_enum=True), nullable=False, index=True
    )
    entity_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Denormalized alongside actor_id so the log stays readable even after
    # the user row is gone or the FK is null (e.g. a failed login for an
    # email that was never a real account).
    actor_email: Mapped[str | None] = mapped_column(String(320), nullable=True)

    # Change payloads. JSONB (not a foreign schema) because the shape of
    # "old"/"new" varies per entity_type — a restaurant edit and a proposed
    # change approval don't share a value schema.
    old_values: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    new_values: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # Free-form extra context (e.g. request IP, source, rejection reason)
    # that doesn't fit old/new but is still worth capturing per action.
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB, nullable=True)

    # Links a data-changing audit row back to the LangGraph run that
    # produced it, when applicable (e.g. AI_EXTRACTION, or a
    # PROPOSED_CHANGE_CREATE that originated from an agent run rather than
    # a human). Not a FK — the agent_runs table doesn't exist yet (AI
    # workflows are a future task) — stored as a plain string id so this
    # column needs no migration once that table lands.
    agent_run_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
