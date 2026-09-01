"""Result types shared by every rule module in core.validation. Kept
separate from the rule modules themselves so `engine.py` and callers
depend on one small, stable vocabulary rather than importing internals
from each rule file.
"""

import enum

from pydantic import BaseModel, ConfigDict, Field

from core.schemas.restaurant import Restaurant


class ValidationSeverity(str, enum.Enum):
    ERROR = "error"
    WARNING = "warning"


class ValidationIssue(BaseModel):
    """One finding from a single rule. `code` is a short, stable
    machine-readable identifier (e.g. "duplicate_dish_name") — useful for
    a reviewer UI to group/filter issues without string-matching
    `message`, which is free text meant for humans."""

    model_config = ConfigDict(extra="forbid")

    field_path: str
    code: str
    message: str
    severity: ValidationSeverity


class CorrectedField(BaseModel):
    """One deterministic, safe correction actually applied. Recorded
    separately from `ValidationIssue` so a caller can tell "this got
    fixed automatically" apart from "this needs a human" at a glance,
    even though both may reference the same field_path."""

    model_config = ConfigDict(extra="forbid")

    field_path: str
    old_value: str | None
    new_value: str | None
    reason: str


class ValidationOutcome(BaseModel):
    """The complete result of running the deterministic validation
    engine once against a Restaurant. `is_valid` is derived — true iff
    `errors` is empty; warnings never affect validity, since a warning by
    definition is something a human should look at, not something that
    blocks the pipeline.

    `corrected_restaurant` is the Restaurant with every safe correction
    in `corrected_fields` already applied (identical to the input when
    `corrected_fields` is empty) — `None` only when the input didn't
    parse as a Restaurant at all (schema_violation errors), since there
    is nothing to have corrected in that case."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    errors: list[ValidationIssue] = Field(default_factory=list)
    warnings: list[ValidationIssue] = Field(default_factory=list)
    corrected_fields: list[CorrectedField] = Field(default_factory=list)
    corrected_restaurant: Restaurant | None = None

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0
