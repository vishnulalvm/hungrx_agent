"""Publish node (Reviewer Workflow Agent 8, part 2: Delta Validation and
Human Final Sync): applies an APPROVED ProposedChange produced by Human
Final Sync to the live restaurant/menu/dish production tables — the
reviewer-workflow counterpart of workflows/collector_workflow/nodes/
publish.py, reusing the exact same re-validation/re-check guarantees
("never overwrite production data without approval") rather than a
separate implementation of that guarantee, since it needs to hold for
every path that can reach these tables, not just the collector
workflow's.

**PATCH-style, transactionally**: unlike the collector workflow's
publish node (which always inserts a brand-new restaurant tree) or an
earlier version of this node (which deleted the whole tree and
re-inserted it fully), this now applies ONLY what state["delta"] (the
APPROVED JSONDelta from json_delta_generation) reports as
added/removed/changed — see nodes/delta_patch.py's `apply_patch` for the
actual row-level mutation logic. An untouched dish's row is never even
flushed, let alone deleted and recreated with a new identity. Everything
still runs inside the caller's existing session/transaction (nothing
here commits), so a failure partway through still leaves nothing
partially applied once the caller rolls back.

Unlike the collector workflow's publish node, this one does NOT refuse a
republish of an already-published entity_id — that guard exists
specifically to stop the *collector* workflow from double-publishing a
brand-new restaurant; the reviewer workflow's entire purpose is updating
an already-published restaurant in place.
"""

import logging
import uuid
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.services.audit_service import AuditService
from core.schemas.audit import AuditAction, AuditEntityType
from core.schemas.diff import JSONDelta
from core.schemas.proposed_change import ProposedChangeStatus
from core.schemas.restaurant import Restaurant
from core.validation.engine import validate
from database.models.restaurant import Restaurant as RestaurantRow
from database.repositories.agent_run_repository import AgentRunRepository
from database.repositories.proposed_change_repository import ProposedChangeRepository
from workflows.reviewer_workflow.nodes.delta_patch import apply_patch
from workflows.reviewer_workflow.state import ReviewerState

logger = logging.getLogger("hungrx.workflows.reviewer.publish")

NODE_NAME = "publish"

PublishNode = Callable[[ReviewerState], Awaitable[dict[str, Any]]]


def build_publish_node(session: AsyncSession) -> PublishNode:
    audit = AuditService(session)
    proposed_changes = ProposedChangeRepository(session)
    agent_runs = AgentRunRepository(session)

    async def _fail(state: ReviewerState, message: str) -> dict[str, Any]:
        logger.error("publish node: %s", message)
        run_id = state.get("agent_run_id")
        if run_id is not None:
            await agent_runs.mark_failed(uuid.UUID(run_id), error_message=message)
        return {"errors": [{"node": NODE_NAME, "message": message}]}

    async def publish_node(state: ReviewerState) -> dict[str, Any]:
        if state.get("human_approval_status") != ProposedChangeStatus.APPROVED:
            return await _fail(
                state, "publish node reached without human_approval_status == APPROVED; refusing to write"
            )

        validated_json = state.get("validated_structured_json")
        proposed_change_id = state.get("proposed_change_id")
        delta = state.get("delta")
        if validated_json is None or proposed_change_id is None or delta is None:
            return await _fail(
                state,
                "ReviewerState.validated_structured_json/proposed_change_id/delta are required "
                "before publish runs",
            )

        outcome = validate(validated_json)
        if not outcome.is_valid:
            return await _fail(
                state,
                "re-validation failed immediately before publish; refusing to write: "
                + "; ".join(f"{issue.field_path}: {issue.message}" for issue in outcome.errors),
            )

        restaurant = Restaurant.model_validate(validated_json)

        existing = await session.get(RestaurantRow, restaurant.id)
        if existing is None:
            return await _fail(
                state,
                f"restaurant {restaurant.id} has no existing production row; the reviewer workflow "
                "only updates an already-published restaurant, it never creates one",
            )

        # PATCH-style: only the rows the approved delta actually names
        # are touched — an untouched dish's row is never even flushed.
        # See nodes/delta_patch.py's apply_patch docstring; this still
        # runs inside the caller's existing session/transaction (no
        # commit here), so a failure partway through leaves nothing
        # partially applied once the caller rolls back.
        await apply_patch(session, restaurant_row=existing, target=restaurant, delta=delta)

        await proposed_changes.update_status(
            uuid.UUID(proposed_change_id), status=ProposedChangeStatus.PUBLISHED
        )

        await audit.log(
            action=AuditAction.PROPOSED_CHANGE_PUBLISH,
            entity_type=AuditEntityType.PROPOSED_CHANGE,
            entity_id=proposed_change_id,
            metadata={"node": NODE_NAME, "restaurant_id": str(restaurant.id), "workflow": "reviewer"},
        )

        run_id = state.get("agent_run_id")
        if run_id is not None:
            await agent_runs.mark_succeeded(uuid.UUID(run_id))

        return {"published_restaurant_id": str(restaurant.id)}

    return publish_node
