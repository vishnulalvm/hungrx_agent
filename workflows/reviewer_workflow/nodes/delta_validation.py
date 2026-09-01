"""Delta Validation node (Reviewer Workflow, stage 4): runs the same
deterministic validation engine (core.validation.validate) the collector
workflow's Deterministic Validation node uses, against the freshly
re-extracted restaurant — not against the delta in isolation, since a
field-level diff has no way to independently check something like
Atwater consistency or duplicate-dish detection without the surrounding
record it belongs to.

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
  - report is_valid/errors/warnings on state for Human Final Sync's
    review payload, so a reviewer sees validation issues before deciding
"""

import logging
import uuid
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.services.audit_service import AuditService
from core.schemas.audit import AuditAction, AuditEntityType
from core.validation.engine import validate
from database.repositories.agent_run_repository import AgentRunRepository
from workflows.reviewer_workflow.state import ReviewerState

logger = logging.getLogger("hungrx.workflows.reviewer.delta_validation")

NODE_NAME = "delta_validation"

DeltaValidationNode = Callable[[ReviewerState], Awaitable[dict[str, Any]]]


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

        update: dict[str, Any] = {
            "validation_result": {
                "is_valid": outcome.is_valid,
                "issues": [
                    {"field_path": issue.field_path, "message": issue.message, "severity": issue.severity.value}
                    for issue in [*outcome.errors, *outcome.warnings]
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
