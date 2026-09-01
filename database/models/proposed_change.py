"""ProposedChange/Approval tables — the review queue this human-in-the-
loop workflow is built around. A ProposedChange is created (PENDING) by
the human_review node before it interrupts the graph, and is the only
record an admin API review endpoint acts on directly; the paused
LangGraph run itself is resumed only as a side effect of that action
(see workflows/collector_workflow/nodes/human_review.py and
apps/api/app/routers/v1/admin/router.py's /reviews endpoints).
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.schemas.proposed_change import ProposedChangeEntityType, ProposedChangeStatus
from database.models.base import Base


class ProposedChange(Base):
    __tablename__ = "proposed_changes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    entity_type: Mapped[ProposedChangeEntityType] = mapped_column(
        Enum(ProposedChangeEntityType, name="proposed_change_entity_type", native_enum=True), nullable=False
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    # Full proposed Restaurant tree (core.schemas.restaurant.Restaurant,
    # model_dump(mode="json")) plus the deterministic validation result —
    # what a reviewer needs to see and, if they choose "edit then
    # approve", what they're allowed to modify before publish. Kept as
    # JSONB rather than a JSONDelta-only record so the review UI/endpoint
    # doesn't need to reconstruct the full proposed state by replaying a
    # diff against the (not-yet-existing, for a new restaurant) current
    # record.
    structured_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    validation_result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    status: Mapped[ProposedChangeStatus] = mapped_column(
        Enum(ProposedChangeStatus, name="proposed_change_status", native_enum=True),
        nullable=False,
        default=ProposedChangeStatus.PENDING,
        index=True,
    )

    # Not a FK to agent_runs — same plain-indexed-column precedent as
    # Source.restaurant_id/AuditLog.agent_run_id elsewhere in this schema.
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    # The LangGraph checkpointer thread this ProposedChange's paused run
    # lives under — required to resume the interrupted graph from an
    # admin API action. Equal to agent_run_id today (one thread per
    # collector run) but kept as its own column rather than reusing
    # agent_run_id directly, since a thread_id is a LangGraph-specific
    # concept, not a domain one.
    thread_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    proposed_change_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("proposed_changes.id", ondelete="CASCADE"), nullable=False, index=True
    )

    decision: Mapped[ProposedChangeStatus] = mapped_column(
        Enum(ProposedChangeStatus, name="proposed_change_status", native_enum=True), nullable=False
    )
    reviewer_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
