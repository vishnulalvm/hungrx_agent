"""API + persistence schemas for the manual/verified restaurant ingestion
queue (apps/api/app/routers/v1/admin/router.py's /ingestion-queue
endpoints). This is a separate path from core/schemas/ingestion.py's
search-based single-restaurant trigger: here the admin already knows and
supplies the restaurant's official URL — see
apps/api/app/services/manual_source_verification.py, which validates
(never searches for) that URL before it's trusted."""

import enum
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class IngestionQueueStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class IngestionQueueItemInput(BaseModel):
    """One row of a bulk-uploaded batch."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    official_url: str = Field(min_length=1, max_length=2048)
    city: str | None = None
    state: str | None = None
    country: str | None = Field(default=None, min_length=2, max_length=2)
    phone: str | None = None


class IngestionQueueBulkUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[IngestionQueueItemInput] = Field(min_length=1)


class IngestionQueueItemSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: uuid.UUID
    batch_id: uuid.UUID
    name: str
    official_url: str
    city: str | None
    state: str | None
    country: str | None
    phone: str | None
    status: IngestionQueueStatus
    restaurant_seed_id: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class IngestionQueueBulkUploadResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: uuid.UUID
    queued_count: int
    skipped_duplicate_names: list[str]
    items: list[IngestionQueueItemSummary]


class IngestionQueueItemEditRequest(BaseModel):
    """All fields optional — a partial update, applied only while the
    target row is still QUEUED (see IngestionQueueRepository.update_if_queued)."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    official_url: str | None = Field(default=None, min_length=1, max_length=2048)
    city: str | None = None
    state: str | None = None
    country: str | None = Field(default=None, min_length=2, max_length=2)
    phone: str | None = None
