"""Result vocabulary for the source-authority resolution service — turning
a restaurant's identity (name, location, etc.) into a verified official
website URL."""

import enum
import uuid

from pydantic import BaseModel, ConfigDict, Field


class ResolutionStatus(str, enum.Enum):
    VERIFIED = "verified"  # a single confident, validated official domain was found
    NEEDS_REVIEW = "needs_review"  # a candidate exists but confidence is too low to auto-trust
    NOT_FOUND = "not_found"  # the provider returned no usable candidate
    REJECTED = "rejected"  # every candidate was an aggregator or otherwise disqualified


class ConfidenceLevel(str, enum.Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class EntityResolutionQuery(BaseModel):
    """What the caller knows about the restaurant, handed to the
    (interface-hidden) external provider to look up candidate websites."""

    model_config = ConfigDict(extra="forbid")

    restaurant_id: uuid.UUID
    name: str = Field(min_length=1)
    city: str | None = None
    state: str | None = None
    country: str | None = None  # ISO 3166-1 alpha-2
    phone: str | None = None


class EntityCandidate(BaseModel):
    """One candidate official-website URL as returned by a provider,
    before any normalization/validation happens on our side."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1)
    provider_confidence: float = Field(ge=0.0, le=1.0)
    provider_name: str


class SourceAuthorityResult(BaseModel):
    """Outcome of resolving a restaurant's official website — always
    returned, even when nothing verified was found, so the caller has a
    typed record of *why* rather than just a null."""

    model_config = ConfigDict(extra="forbid")

    restaurant_id: uuid.UUID
    status: ResolutionStatus
    confidence: ConfidenceLevel | None = None
    resolved_url: str | None = None
    rejected_candidates: list[str] = Field(default_factory=list)
    reason: str | None = None
    source_id: uuid.UUID | None = None  # set once persisted
