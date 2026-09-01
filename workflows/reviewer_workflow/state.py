"""Typed state for the reviewer LangGraph workflow.

Distinct from workflows/collector_workflow/state.py's CollectorState:
the Collector Workflow takes a restaurant from "we know its name" to
"first published"; the Reviewer Workflow takes an already-published
restaurant and checks whether its live source has changed since, and if
so, proposes an update for a human to approve — same review-queue
infrastructure (core.schemas.proposed_change, human_review-style
interrupt/resume), but the input is "an existing Restaurant + its
verified Source" rather than a brand-new one, and the AI/diff work only
ever runs when something has actually changed at the source.
"""

import operator
from typing import Annotated, TypedDict

from core.schemas.diff import JSONDelta
from core.schemas.proposed_change import ProposedChangeStatus
from core.schemas.restaurant import Restaurant
from core.schemas.source import Source, SourceSnapshot


class ValidationIssue(TypedDict):
    field_path: str
    message: str
    severity: str  # "error" | "warning"


class ValidationResult(TypedDict, total=False):
    is_valid: bool
    issues: list[ValidationIssue]


class WorkflowError(TypedDict):
    node: str
    message: str


def _keep_last(_current, new):
    """Reducer for state fields where a later node's value should
    replace, not append to, the earlier one — same convention as
    workflows/collector_workflow/state.py's _keep_last."""
    return new


class ReviewerState(TypedDict, total=False):
    """total=False: fields are populated progressively as the run moves
    through Temporal Hash Polling -> Targeted Re-Extraction -> JSON Delta
    Generation -> Delta Validation -> Human Final Sync; required-ness at
    a given point is a node-ordering guarantee, not encoded in the type.
    """

    # Identity — what this run is reviewing.
    agent_run_id: str
    restaurant_id: str
    restaurant: Restaurant  # the currently PUBLISHED restaurant, loaded read-only
    source: Source

    # Temporal Hash Polling output.
    previous_content_hash: str | None
    current_content_hash: str
    hash_changed: bool
    polled_snapshot: SourceSnapshot

    # Targeted Re-Extraction output — only populated when hash_changed.
    # Reuses _keep_last (not operator.add) since a re-run should replace,
    # not accumulate, the previous attempt's captures.
    reextraction_snapshots: Annotated[list[SourceSnapshot], _keep_last]
    reextracted_structured_json: dict

    # Maps a dish id (str(uuid)) or the sentinel key "restaurant_profile"
    # to the list of SourceSnapshot ids (str) that item's re-extracted
    # data was read from — the AI output's own per-item
    # source_snapshot_ids (core.schemas.extraction_output.ExtractedDish/
    # ExtractedRestaurantProfile), carried forward on state since the
    # real domain schemas (core.schemas.restaurant.Restaurant/menu.Dish)
    # don't have a source_snapshot_ids field of their own. Read by
    # json_delta_generation to attach source references onto each
    # FieldDelta it produces.
    reextraction_source_refs: dict[str, list[str]]

    # JSON Delta Generation output.
    delta: JSONDelta

    # JSON Delta Generation output.
    delta: JSONDelta

    # Delta Validation output.
    validation_result: ValidationResult
    validated_structured_json: dict

    # Human Final Sync — identity of the ProposedChange DB row and the
    # approval outcome, same pattern as CollectorState.proposed_change_id/
    # human_approval_status. The admin decision itself flows through
    # LangGraph's interrupt() return value, not a separate state field.
    proposed_change_id: str
    human_approval_status: ProposedChangeStatus

    # Publish output — set only once Human Final Sync's resumed decision
    # has actually been applied to the production tables.
    published_restaurant_id: str

    # Accumulates across every node — later nodes append, never overwrite.
    errors: Annotated[list[WorkflowError], operator.add]
