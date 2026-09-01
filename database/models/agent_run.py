import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.schemas.agent_run import AgentRunStatus, AgentWorkflowType
from database.models.base import Base


class AgentRun(Base):
    """One execution of a LangGraph workflow (collector or reviewer).
    Created at the start of a run so every downstream record it produces
    (a Source, a ProposedChange, an AuditLog row) has a stable run id to
    reference, and updated in place as the run progresses through
    RUNNING -> SUCCEEDED/FAILED/CANCELLED.

    `restaurant_id` is not yet a foreign key, matching the same precedent
    as Source.restaurant_id and AuditLog.agent_run_id — the restaurants
    table doesn't exist yet.
    """

    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    workflow_type: Mapped[AgentWorkflowType] = mapped_column(
        Enum(AgentWorkflowType, name="agent_workflow_type", native_enum=True), nullable=False, index=True
    )
    restaurant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    status: Mapped[AgentRunStatus] = mapped_column(
        Enum(AgentRunStatus, name="agent_run_status", native_enum=True),
        nullable=False,
        default=AgentRunStatus.PENDING,
        index=True,
    )

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
