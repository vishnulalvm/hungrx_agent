"""Typed representation of a field-level change, e.g. as produced by
DeepDiff over two schema dicts. `ProposedChange.delta` uses this instead of
a raw dict so reviewers' UIs and audit records get a consistent shape
regardless of which module produced the diff."""

import enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DeltaOp(str, enum.Enum):
    ADDED = "added"
    REMOVED = "removed"
    CHANGED = "changed"


class FieldDelta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str  # e.g. "menus[0].categories[1].dishes[2].nutrition.macros.calories"
    op: DeltaOp
    old_value: Any = None
    new_value: Any = None


class JSONDelta(BaseModel):
    """A full set of field-level changes between two versions of an
    entity."""

    model_config = ConfigDict(extra="forbid")

    fields: list[FieldDelta] = Field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return len(self.fields) == 0
