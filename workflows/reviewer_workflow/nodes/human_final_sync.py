"""Human Final Sync node (Reviewer Workflow, stage 5): creates a
ProposedChange record carrying the field-level delta plus the fully
re-extracted/validated restaurant, then pauses the graph via LangGraph's
`interrupt()` until an admin resumes it with a decision — same
interrupt/resume mechanics as the collector workflow's
workflows/collector_workflow/nodes/human_review.py, reused here rather
than reimplemented, since the review-queue infrastructure (ProposedChange/
Approval, apps/api/app/services/review_service.py's resume, the
/api/v1/admin/reviews endpoints) doesn't care which workflow produced a
given ProposedChange — only that its `thread_id` can resume the paused
run that created it.

Idempotent record creation follows the exact same pattern
human_review.py documents: LangGraph replays this node from the top on
resume with the *same pre-interrupt state*, so record creation is gated
on a durable lookup (ProposedChangeRepository.get_by_thread_id), never on
anything this node itself would have returned after interrupting.

What makes this "final sync" rather than a second full review: the
ProposedChange this node creates carries `delta` (what changed) as well
as the full re-extracted/validated structured_json (what the record
should become) — a reviewer can act on either view, but approval always
applies the *whole* validated structured_json, same as the collector
workflow's publish node does; there's no separate "apply just these
fields" path, since a partial field-level apply would risk producing a
restaurant that itself never passed schema/nutrition/allergen validation
as a whole.
"""

import logging
from typing import Any, Awaitable, Callable

from langgraph.types import interrupt
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.services.audit_service import AuditService
from core.schemas.audit import AuditAction, AuditEntityType
from core.schemas.proposed_change import ProposedChangeEntityType, ProposedChangeStatus
from database.models.proposed_change import ProposedChange
from database.repositories.proposed_change_repository import ProposedChangeRepository
from workflows.reviewer_workflow.state import ReviewerState

logger = logging.getLogger("hungrx.workflows.reviewer.human_final_sync")

NODE_NAME = "human_final_sync"

HumanFinalSyncNode = Callable[[ReviewerState], Awaitable[dict[str, Any]]]


def build_human_final_sync_node(session: AsyncSession) -> HumanFinalSyncNode:
    audit = AuditService(session)
    proposed_changes = ProposedChangeRepository(session)

    async def human_final_sync_node(state: ReviewerState) -> dict[str, Any]:
        restaurant = state.get("restaurant")
        validated_json = state.get("validated_structured_json")
        validation_result = state.get("validation_result")
        delta = state.get("delta")
        agent_run_id = state.get("agent_run_id")

        if restaurant is None or validated_json is None or not agent_run_id:
            message = (
                "ReviewerState.restaurant/validated_structured_json/agent_run_id are required "
                "before human_final_sync runs (delta_validation must succeed first, and "
                "agent_run_id doubles as the checkpoint thread identity this node depends on)"
            )
            logger.error("human_final_sync node: %s", message)
            return {"errors": [{"node": NODE_NAME, "message": message}]}

        record = await proposed_changes.get_by_thread_id(agent_run_id)

        if record is None:
            record = await proposed_changes.create(
                entity_type=ProposedChangeEntityType.RESTAURANT,
                entity_id=restaurant.id,
                structured_json=validated_json,
                validation_result=validation_result or {},
                agent_run_id=_safe_uuid(agent_run_id),
                source_id=state["source"].id if state.get("source") else None,
                thread_id=agent_run_id,
            )
            await audit.log(
                action=AuditAction.PROPOSED_CHANGE_CREATE,
                entity_type=AuditEntityType.PROPOSED_CHANGE,
                entity_id=str(record.id),
                metadata={
                    "node": NODE_NAME,
                    "restaurant_id": str(restaurant.id),
                    "delta_field_count": len(delta.fields) if delta is not None else None,
                },
            )

        review_task = {
            "proposed_change_id": str(record.id),
            "restaurant_id": str(restaurant.id),
            "restaurant_name": restaurant.name,
            "is_valid": (validation_result or {}).get("is_valid"),
            "issue_count": len((validation_result or {}).get("issues", [])),
            "delta": delta.model_dump(mode="json") if delta is not None else {"fields": []},
        }
        decision = interrupt(review_task)

        return _apply_decision(decision, record=record)

    return human_final_sync_node


def _safe_uuid(value: str):
    import uuid

    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def _apply_decision(decision: Any, *, record: ProposedChange) -> dict[str, Any]:
    if not isinstance(decision, dict) or not decision:
        return {
            "errors": [{"node": NODE_NAME, "message": "human_final_sync resumed with no usable decision"}]
        }

    action = decision.get("action")
    update: dict[str, Any] = {"proposed_change_id": str(record.id)}

    if action == "approve":
        update["human_approval_status"] = ProposedChangeStatus.APPROVED
    elif action == "edit_then_approve":
        update["human_approval_status"] = ProposedChangeStatus.APPROVED
        if decision.get("edited_structured_json") is not None:
            update["validated_structured_json"] = decision["edited_structured_json"]
    elif action == "reject":
        update["human_approval_status"] = ProposedChangeStatus.REJECTED
    else:
        return {
            "errors": [
                {"node": NODE_NAME, "message": f"human_final_sync received an unrecognized decision action: {action!r}"}
            ]
        }

    return update
