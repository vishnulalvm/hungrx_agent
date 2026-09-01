"""Provenance schemas: where a piece of data came from and what was
captured at the time. The crawler infrastructure (a separate future task)
will produce these; this module only defines their shape."""

import enum
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SourceType(str, enum.Enum):
    RESTAURANT_WEBSITE = "restaurant_website"
    THIRD_PARTY_MENU_PROVIDER = "third_party_menu_provider"
    PDF_MENU = "pdf_menu"
    MANUAL_ENTRY = "manual_entry"


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    restaurant_id: uuid.UUID
    source_type: SourceType
    url: str = Field(min_length=1)
    is_verified_domain: bool = False


class SnapshotContentType(str, enum.Enum):
    HTML = "html"
    PDF = "pdf"
    SCREENSHOT = "screenshot"


class SourceSnapshot(BaseModel):
    """One captured fetch of a Source at a point in time. `content_hash` is
    the SHA-256 hex digest of the raw captured bytes (computed by the
    crawler, not here) — used to detect "nothing changed since last crawl"
    without re-diffing the full content."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    source_id: uuid.UUID
    content_type: SnapshotContentType
    content_hash: str = Field(min_length=64, max_length=64)  # SHA-256 hex digest
    storage_path: str = Field(min_length=1)  # where the raw capture is persisted
    fetched_at: datetime
    http_status: int | None = None
    content_length_bytes: int | None = Field(default=None, ge=0)
