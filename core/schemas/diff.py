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

    # Which captured source materials (SourceSnapshot.id, as strings)
    # the new_value came from — traces this specific change back to the
    # raw page(s) it was read off of, same provenance contract
    # core.schemas.extraction_output.ExtractedDish.source_snapshot_ids
    # already establishes for AI-extracted dishes. Empty for a change
    # this repo can't attribute to a specific snapshot (e.g. a REMOVED
    # entry, where there's no new source material to point to).
    source_snapshot_ids: list[str] = Field(default_factory=list)


class JSONDelta(BaseModel):
    """A full set of field-level changes between two versions of an
    entity."""

    model_config = ConfigDict(extra="forbid")

    fields: list[FieldDelta] = Field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return len(self.fields) == 0
