"""Delta Validation node (Reviewer Workflow Agent 8, part 1: Delta
Validation and Human Final Sync): runs the same deterministic validation
engine (core.validation.validate) the collector workflow's Deterministic
Validation node uses, against the freshly re-extracted restaurant — not
against the delta in isolation, since a field-level diff has no way to
independently check something like Atwater consistency or duplicate-dish
detection without the surrounding record it belongs to. The engine
always runs against the *whole* re-extracted record (running it against
a partial/scoped record would itself be unsafe — duplicate-dish
detection, for instance, has to see every dish to find a duplicate); what
IS scoped to changed/new data is what gets *reported* to the reviewer.

Responsibilities:
  - validate state["reextracted_structured_json"] with the exact same
    rule engine used pre-publish (schema, nutrition/Atwater, allergen
    taxonomy, price, required-field, duplicate, impossible-value) — one
    validation engine for the whole codebase, not a reviewer-specific
    reimplementation
  - only ever replace structured_json with a safe correction
    (core.validation.safe_corrections — pure formatting, never a
    numeric/monetary/AI-inferred value), same "never silently change
    AI-generated data" guarantee deterministic_validation.py documents
  - "validate only changed/new data where safe": scope which issues are
    actually surfaced to the reviewer down to fields the delta reports as
    ADDED/CHANGED (state["delta"], from json_delta_generation) — an issue
    on a dish nobody touched this run (pre-existing in the currently
    published data, unrelated to whatever changed at the source) is noise
    a reviewer shouldn't have to re-triage on every single reviewer run;
    it was already true before this run and isn't this run's job to
    surface. Restaurant-level/schema-level issues (no specific dish
    index in their field_path) are never filtered — those aren't
    attributable to one item, so they can't be safely scoped away.
    `is_valid`/whether a correction was applied still reflects the FULL
    validation run (never scoped) — scoping only affects which issues are
    reported for review, not whether the record is safe to proceed with.
  - report is_valid/errors/warnings on state for Human Final Sync's
    review payload, so a reviewer sees validation issues before deciding
"""

import logging
import re
import uuid
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.services.audit_service import AuditService
from core.schemas.audit import AuditAction, AuditEntityType
from core.schemas.diff import DeltaOp, JSONDelta
from core.validation.engine import validate
from core.validation.result import ValidationIssue
from database.repositories.agent_run_repository import AgentRunRepository
from workflows.reviewer_workflow.state import ReviewerState

logger = logging.getLogger("hungrx.workflows.reviewer.delta_validation")

NODE_NAME = "delta_validation"

DeltaValidationNode = Callable[[ReviewerState], Awaitable[dict[str, Any]]]

_DISH_PREFIX_RE = re.compile(r"^(menus\[\d+\]\.categories\[\d+\](?:\.children\[\d+\])*\.dishes\[\d+\])")


def _touched_dish_prefixes(delta: JSONDelta | None) -> set[str] | None:
    """Returns the set of `menus[N].categories[N]...dishes[N]` path
    prefixes the delta reports as ADDED or CHANGED, or None when there's
    no delta at all (nothing to scope against — every issue is reported,
    same as before this node had scoping). A dish that was REMOVED has
    nothing left to validate, so removals don't add to this set."""
    if delta is None:
        return None
    prefixes: set[str] = set()
    for field in delta.fields:
        if field.op not in (DeltaOp.ADDED, DeltaOp.CHANGED):
            continue
        match = _DISH_PREFIX_RE.match(field.path)
        if match:
            prefixes.add(match.group(1))
    return prefixes


def _is_reportable(issue: ValidationIssue, *, touched_dish_prefixes: set[str] | None) -> bool:
    if touched_dish_prefixes is None:
        return True
    match = _DISH_PREFIX_RE.match(issue.field_path)
    if match is None:
        # Not a dish-specific issue (a restaurant-level or schema-level
        # finding) — never attributable to "changed or not," always kept.
        return True
    return match.group(1) in touched_dish_prefixes


def build_delta_validation_node(session: AsyncSession) -> DeltaValidationNode:
    audit = AuditService(session)
    agent_runs = AgentRunRepository(session)

    async def delta_validation_node(state: ReviewerState) -> dict[str, Any]:
        reextracted_json = state.get("reextracted_structured_json")

        if reextracted_json is None:
            message = (
                "ReviewerState.reextracted_structured_json is required before delta_validation "
                "runs (targeted_reextraction must succeed first)"
            )
            logger.error("delta_validation node: %s", message)
            return {"errors": [{"node": NODE_NAME, "message": message}]}

        outcome = validate(reextracted_json)

        validated_json = (
            outcome.corrected_restaurant.model_dump(mode="json")
            if outcome.corrected_restaurant is not None
            else reextracted_json
        )

        touched_dish_prefixes = _touched_dish_prefixes(state.get("delta"))
        reportable_issues = [
            issue
            for issue in (*outcome.errors, *outcome.warnings)
            if _is_reportable(issue, touched_dish_prefixes=touched_dish_prefixes)
        ]

        update: dict[str, Any] = {
            "validation_result": {
                # is_valid always reflects the FULL, unscoped validation
                # run — scoping only narrows what's *reported*, never
                # whether the data is actually safe to proceed with.
                "is_valid": outcome.is_valid,
                "issues": [
                    {"field_path": issue.field_path, "message": issue.message, "severity": issue.severity.value}
                    for issue in reportable_issues
                ],
            },
            "validated_structured_json": validated_json,
        }

        run_id = state.get("agent_run_id")
        if run_id is not None and (not outcome.is_valid or outcome.corrected_fields):
            await audit.log(
                action=AuditAction.AGENT_RUN_TRIGGER,
                entity_type=AuditEntityType.AGENT_RUN,
                entity_id=run_id,
                metadata={
                    "node": NODE_NAME,
                    "is_valid": outcome.is_valid,
                    "error_count": len(outcome.errors),
                    "warning_count": len(outcome.warnings),
                    "reported_issue_count": len(reportable_issues),
                    "corrected_fields": [field.field_path for field in outcome.corrected_fields],
                },
            )
            if not outcome.is_valid:
                await agent_runs.mark_failed(
                    uuid.UUID(run_id),
                    error_message=f"delta_validation found {len(outcome.errors)} error(s) in the re-extracted data",
                )

        return update

    return delta_validation_node
