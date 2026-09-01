"""API response/request schemas for the admin review queue
(apps/api/app/routers/v1/admin/router.py's /reviews endpoints)."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from core.schemas.proposed_change import ProposedChangeEntityType, ProposedChangeStatus


class ReviewSummary(BaseModel):
    """One row of the pending-reviews list — enough to populate a queue
    UI without pulling the full structured_json payload per row."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    entity_type: ProposedChangeEntityType
    entity_id: uuid.UUID
    status: ProposedChangeStatus
    agent_run_id: uuid.UUID | None
    created_at: datetime


class ReviewDetail(BaseModel):
    """Full detail for one review — the proposed data itself plus the
    deterministic validation findings a reviewer needs to make a
    decision."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    entity_type: ProposedChangeEntityType
    entity_id: uuid.UUID
    status: ProposedChangeStatus
    structured_json: dict[str, Any]
    validation_result: dict[str, Any]
    agent_run_id: uuid.UUID | None
    source_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class ReviewDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=2000)


class ReviewEditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    edited_structured_json: dict[str, Any]
    reason: str | None = Field(default=None, max_length=2000)


class ReviewActionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposed_change_id: uuid.UUID
    status: ProposedChangeStatus
    published_restaurant_id: uuid.UUID | None = None
    errors: list[dict[str, Any]] = Field(default_factory=list)
