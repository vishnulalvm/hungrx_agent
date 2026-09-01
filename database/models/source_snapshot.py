"""Durable persistence for core.schemas.source.SourceSnapshot. The
collector workflow (Extraction node) only ever kept SourceSnapshots on
in-memory LangGraph state for the duration of one run; nothing recorded
what the *last* captured hash for a Source was once that run finished.

The reviewer workflow's Temporal Hash Polling node needs exactly that —
"has this source's content changed since the last time we looked?" —
answered across separate runs, potentially days apart, so this table is
what makes that question answerable without re-fetching and re-diffing
full page content every time.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.schemas.source import SnapshotContentType
from database.models.base import Base


class SourceSnapshotRow(Base):
    __tablename__ = "source_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False, index=True
    )

    content_type: Mapped[SnapshotContentType] = mapped_column(
        Enum(SnapshotContentType, name="snapshot_content_type", native_enum=True), nullable=False
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    storage_path: Mapped[str] = mapped_column(String(2048), nullable=False)

    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_length_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
