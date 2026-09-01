"""Typed state for the collector LangGraph workflow.

One CollectorState flows through every node (Source Authority -> Extraction
-> Multimodal Translation -> Deterministic Validation -> Human Review ->
Publish). Fields are additive as the run progresses — an early node
(Source Authority) only ever populates its own slice; later nodes read
what came before and add their own. Nothing here is mutated in place by
node functions themselves: LangGraph nodes return a partial state dict
that gets merged into the run's state, which is why this is a TypedDict
(the shape LangGraph's StateGraph expects) built from the same Pydantic
schemas used everywhere else in the codebase, not a parallel vocabulary.
"""

import operator
from typing import Annotated, TypedDict

from core.schemas.diff import JSONDelta
from core.schemas.proposed_change import ProposedChange, ProposedChangeStatus
from core.schemas.restaurant import Restaurant
from core.schemas.source import Source, SourceSnapshot


class ExtractionResult(TypedDict, total=False):
    """Raw output of the extraction node, before multimodal translation
    normalizes it into a JSONDelta against the current Restaurant record.
    Kept loosely typed (dict payload) here because the extraction node's
    own output shape is model/provider-dependent — MultimodalTranslation
    is exactly the node responsible for turning this into something
    strictly typed (a JSONDelta), so CollectorState itself doesn't need to
    pin down the raw shape.
    """

    raw_payload: dict
    model_name: str
    confidence: float


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


def _keep_last(_current: list, new: list) -> list:
    """Reducer for list-typed state fields where a later node's value
    should replace, not append to, the earlier one — LangGraph requires an
    explicit reducer for any field re-assigned across node returns."""
    return new


class CollectorState(TypedDict, total=False):
    """total=False: every field is optional because early nodes haven't
    populated later-stage fields yet (e.g. `extraction_result` doesn't
    exist until after the Extraction node runs) — required-ness of a given
    field at a given point in the graph is a node-ordering guarantee, not
    something the type system encodes here.
    """

    # Identity
    agent_run_id: str
    restaurant: Restaurant

    # Source Authority output
    source_url: str
    source: Source

    # Crawl capture. `source_snapshot` is the primary/first captured page
    # (kept for backwards-compatible single-snapshot access);
    # `source_snapshots` is the full set captured by the Extraction node
    # (source page plus any discovered menu/nutrition pages/PDFs).
    source_snapshot: SourceSnapshot
    source_snapshots: Annotated[list[SourceSnapshot], _keep_last]

    # Extraction output (raw, pre-normalization)
    extraction_result: ExtractionResult

    # Multimodal Translation output (typed, structured)
    structured_json: dict

    # Deterministic Validation output
    validation_result: ValidationResult

    # Human Review output
    proposed_changes: Annotated[list[ProposedChange], _keep_last]
    human_approval_status: ProposedChangeStatus

    # Accumulates across every node — later nodes append, never overwrite,
    # so a failure downstream doesn't erase what an earlier node reported.
    errors: Annotated[list[WorkflowError], operator.add]
