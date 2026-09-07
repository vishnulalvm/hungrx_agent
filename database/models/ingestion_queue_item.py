import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.schemas.ingestion_queue import IngestionQueueStatus
from database.models.base import Base


class IngestionQueueItem(Base):
    """One admin-supplied, pre-verified restaurant awaiting the manual
    ingestion pipeline (verify URL -> crawl -> collector workflow). Rows
    are created in bulk from one upload (grouped by `batch_id`) and
    processed strictly one at a time by
    apps/worker/app/jobs/ingestion_queue_dispatcher.py, which claims the
    oldest QUEUED row and transitions it through RUNNING to
    SUCCEEDED/FAILED.

    `restaurant_id` is deliberately not stored here — no restaurant row
    exists until an approved review publishes one, and the restaurant_id
    this queue item's pipeline run uses is derived from
    `restaurant_seed_id` the same way apps/worker/app/jobs/
    restaurant_ingestion.py already does, so no persisted mapping is
    needed to look it up.
    """

    __tablename__ = "ingestion_queue_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    official_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    state: Mapped[str | None] = mapped_column(String(120), nullable=True)
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)

    status: Mapped[IngestionQueueStatus] = mapped_column(
        Enum(IngestionQueueStatus, name="ingestion_queue_status", native_enum=True),
        nullable=False,
        default=IngestionQueueStatus.QUEUED,
        index=True,
    )
    restaurant_seed_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
