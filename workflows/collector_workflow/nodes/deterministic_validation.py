"""Deterministic Validation node (Collector Workflow Agent 4): runs
non-AI, rule-based checks against `structured_json` and records a
ValidationResult. "Deterministic" is the operative word — no model call
happens here, only code the team can reason about and unit test directly
(see core.validation for the actual rule engine; this node is a thin
LangGraph adapter around it).

Responsibilities (per the collector workflow's Agent 4 spec):
  - Pydantic validation: core.validation.schema_validation re-validates
    structured_json against Restaurant
  - nutrition constraints / Atwater calculation checks: core.validation.
    nutrition_rules
  - allergen taxonomy checks: core.validation.allergen_rules
  - price validation: core.validation.price_rules
  - required-field validation: core.validation.required_fields
  - duplicate detection: core.validation.duplicate_detection
  - impossible-value detection: core.validation.impossible_value_detection
  - deterministic: core.validation.engine.validate has no I/O, no model
    call, no randomness
  - returns valid/invalid, errors, warnings, and corrected fields only
    when a correction is deterministically safe (core.validation.
    safe_corrections — formatting-only, never a numeric/AI-inferred
    value)
  - never silently changes AI-generated data: `structured_json` on state
    is only ever replaced by the *corrected* Restaurant when at least one
    safe correction was actually applied, and every correction is
    recorded on validation_result.corrected_fields so nothing changes
    without a visible record of what changed and why

A LangGraph node function's signature is fixed to `(state) -> partial
state update`; `build_deterministic_validation_node` is a factory (same
pattern as the other collector nodes) closing over the DB session for
audit logging on failure — this node otherwise has no external
dependencies (no DB reads/writes for the validation itself, no AI
provider, no storage), which is exactly what "independent of the LLM"
means in practice.
"""

import logging
import uuid
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.services.audit_service import AuditService
from core.schemas.audit import AuditAction, AuditEntityType
from core.validation import validate
from core.validation.result import ValidationSeverity
from database.repositories.agent_run_repository import AgentRunRepository
from workflows.collector_workflow.state import CollectorState, ValidationIssue as StateValidationIssue

logger = logging.getLogger("hungrx.workflows.collector.deterministic_validation")

NODE_NAME = "deterministic_validation"

DeterministicValidationNode = Callable[[CollectorState], Awaitable[dict[str, Any]]]


def _to_state_issue(issue) -> StateValidationIssue:
    return {"field_path": issue.field_path, "message": issue.message, "severity": issue.severity.value}


def build_deterministic_validation_node(session: AsyncSession) -> DeterministicValidationNode:
    audit = AuditService(session)
    agent_runs = AgentRunRepository(session)

    async def deterministic_validation_node(state: CollectorState) -> dict[str, Any]:
        structured_json = state.get("structured_json")

        if structured_json is None:
            message = (
                "CollectorState.structured_json is required before the deterministic_validation "
                "node runs (multimodal_translation must succeed first)"
            )
            logger.error("deterministic_validation node: %s", message)
            return {"errors": [{"node": NODE_NAME, "message": message}]}

        outcome = validate(structured_json)

        validation_result = {
            "is_valid": outcome.is_valid,
            "issues": [
                _to_state_issue(issue) for issue in (*outcome.errors, *outcome.warnings)
            ],
        }

        update: dict[str, Any] = {"validation_result": validation_result}

        # Only ever replace structured_json when a correction was
        # actually applied — re-serializing an unchanged Restaurant would
        # be a no-op in content but a needless churn of the state value;
        # more importantly, this keeps "structured_json changed" a
        # reliable signal that a recorded, safe correction happened.
        if outcome.corrected_fields and outcome.corrected_restaurant is not None:
            update["structured_json"] = outcome.corrected_restaurant.model_dump(mode="json")

        run_id = state.get("agent_run_id")
        error_issues = [issue for issue in outcome.errors if issue.severity == ValidationSeverity.ERROR]

        if run_id is not None and (error_issues or outcome.corrected_fields):
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
            failure_message = (
                f"deterministic_validation found {len(outcome.errors)} error(s): "
                f"{'; '.join(issue.message for issue in outcome.errors[:5])}"
            )
            logger.warning(failure_message)
            if run_id is not None:
                await agent_runs.mark_failed(uuid.UUID(run_id), error_message=failure_message)
            update["errors"] = [{"node": NODE_NAME, "message": failure_message}]

        return update

    return deterministic_validation_node
