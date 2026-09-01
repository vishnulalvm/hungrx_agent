"""A pending, reviewable modification to restaurant/menu/dish data —
either AI-proposed (agent_run_id set) or human-authored (agent_run_id
null) — plus its approval/rejection history."""

import enum
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from core.schemas.diff import JSONDelta


class ProposedChangeEntityType(str, enum.Enum):
    RESTAURANT = "restaurant"
    RESTAURANT_LOCATION = "restaurant_location"
    MENU = "menu"
    MENU_CATEGORY = "menu_category"
    DISH = "dish"


class ProposedChangeStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    PUBLISHED = "published"


class ProposedChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    entity_type: ProposedChangeEntityType
    entity_id: uuid.UUID

    delta: JSONDelta
    status: ProposedChangeStatus = ProposedChangeStatus.PENDING

    # Set when this change originated from an AI run rather than a human
    # editing directly in the admin dashboard.
    agent_run_id: uuid.UUID | None = None
    source_id: uuid.UUID | None = None

    created_by_user_id: uuid.UUID | None = None
    created_at: datetime | None = None


class Approval(BaseModel):
    """One decision (approve/reject/publish) against a ProposedChange —
    kept as its own record, separate from ProposedChange.status, so a
    change's full decision history survives even if it's later
    re-reviewed."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    proposed_change_id: uuid.UUID
    decision: ProposedChangeStatus  # APPROVED, REJECTED, or PUBLISHED
    reviewer_user_id: uuid.UUID
    reason: str | None = Field(default=None, max_length=2000)
    decided_at: datetime | None = None
