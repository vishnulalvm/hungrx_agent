import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.schemas.source import SourceType
from database.models.base import Base


class Source(Base):
    """A verified provenance record — currently written by the
    source-authority resolution service for a restaurant's official
    website. `restaurant_id` is not yet a foreign key: the restaurants
    table doesn't exist (restaurant persistence is a separate future
    task), so this stays a plain indexed UUID column, matching the same
    precedent as AuditLog.agent_run_id — it needs no migration once that
    table lands.
    """

    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    restaurant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    source_type: Mapped[SourceType] = mapped_column(
        Enum(SourceType, name="source_type", native_enum=True), nullable=False
    )
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    is_verified_domain: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
