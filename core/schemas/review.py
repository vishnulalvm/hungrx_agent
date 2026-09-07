"""API response/request schemas for the admin review queue
(apps/api/app/routers/v1/admin/router.py's /reviews endpoints)."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.schemas.proposed_change import ProposedChangeEntityType, ProposedChangeStatus


def _display_name_from_structured_json(structured_json: Any) -> str | None:
    """Both workflows' human-review nodes store the entity's full mapped
    schema dump as structured_json (see
    workflows/collector_workflow/nodes/multimodal_translation.py's
    `mapped_restaurant.model_dump(mode="json")`), so `name` is present at
    the top level for a RESTAURANT entity. Other entity types
    (menu_category/dish/...) don't have a reliable top-level `name` key,
    so this deliberately returns None rather than guessing at one."""
    if not isinstance(structured_json, dict):
        return None
    name = structured_json.get("name")
    return name if isinstance(name, str) else None


class ReviewSummary(BaseModel):
    """One row of the pending-reviews list. `name` is derived from
    structured_json (not stored separately) so the queue UI can show
    which restaurant a row is for instead of just its entity type."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    entity_type: ProposedChangeEntityType
    entity_id: uuid.UUID
    status: ProposedChangeStatus
    name: str | None = None
    agent_run_id: uuid.UUID | None
    created_at: datetime

    @model_validator(mode="before")
    @classmethod
    def _derive_name(cls, data: Any) -> Any:
        # `data` is either the ProposedChange ORM row (from_attributes) or
        # a plain dict — either way, structured_json isn't itself a field
        # on this schema, so it has to be read off here before pydantic
        # drops anything it doesn't recognize.
        structured_json = getattr(data, "structured_json", None)
        if structured_json is None and isinstance(data, dict):
            structured_json = data.get("structured_json")
        name = _display_name_from_structured_json(structured_json)

        if isinstance(data, dict):
            return {**data, "name": name}
        return {
            field: getattr(data, field)
            for field in ("id", "entity_type", "entity_id", "status", "agent_run_id", "created_at")
        } | {"name": name}


class ReviewDetail(BaseModel):
    """Full detail for one review — the proposed data itself plus the
    deterministic validation findings a reviewer needs to make a
    decision. `name` is derived from structured_json, which this schema
    already carries as a field."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    entity_type: ProposedChangeEntityType
    entity_id: uuid.UUID
    status: ProposedChangeStatus
    name: str | None = None
    structured_json: dict[str, Any]
    validation_result: dict[str, Any]
    agent_run_id: uuid.UUID | None
    source_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def _derive_name(self) -> "ReviewDetail":
        self.name = _display_name_from_structured_json(self.structured_json)
        return self


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
