"""Publish node (Reviewer Workflow, terminal stage): applies an approved
ProposedChange produced by Human Final Sync to the live restaurant/menu/
dish production tables — the reviewer-workflow counterpart of
workflows/collector_workflow/nodes/publish.py, reusing the exact same
RestaurantRepository.persist_tree/re-validation/re-check guarantees
rather than a separate implementation, since "unapproved data never
reaches production tables" needs to hold for every path that can reach
these tables, not just the collector workflow's.

Unlike the collector workflow's publish node, this one does NOT refuse a
republish of an already-published entity_id — that guard exists
specifically to stop the *collector* workflow from double-publishing a
brand-new restaurant; the reviewer workflow's entire purpose is updating
an already-published restaurant in place (persist_tree's insert-only
behavior means this replaces the same-id rows via ON CONFLICT-free
re-insertion, so see the docstring below on why the update path here
deletes the prior tree before re-inserting, rather than reusing
persist_tree's raw insert semantics unmodified).
"""

import logging
import uuid
from typing import Any, Awaitable, Callable

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.services.audit_service import AuditService
from core.schemas.audit import AuditAction, AuditEntityType
from core.schemas.proposed_change import ProposedChangeStatus
from core.schemas.restaurant import Restaurant
from core.validation.engine import validate
from database.models.restaurant import Restaurant as RestaurantRow
from database.repositories.agent_run_repository import AgentRunRepository
from database.repositories.proposed_change_repository import ProposedChangeRepository
from database.repositories.restaurant_repository import RestaurantRepository
from workflows.reviewer_workflow.state import ReviewerState

logger = logging.getLogger("hungrx.workflows.reviewer.publish")

NODE_NAME = "publish"

PublishNode = Callable[[ReviewerState], Awaitable[dict[str, Any]]]


def build_publish_node(session: AsyncSession) -> PublishNode:
    audit = AuditService(session)
    proposed_changes = ProposedChangeRepository(session)
    agent_runs = AgentRunRepository(session)
    restaurants = RestaurantRepository(session)

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
        if validated_json is None or proposed_change_id is None:
            return await _fail(
                state,
                "ReviewerState.validated_structured_json/proposed_change_id are required before publish runs",
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

        # persist_tree only ever inserts; a reviewer-workflow publish is
        # always an update to an existing restaurant, so the prior tree
        # is deleted first (the ORM's cascade="all, delete-orphan" takes
        # care of locations/menus/categories/dishes) and the validated
        # tree re-inserted with the same restaurant id — both operations
        # happen in the same, uncommitted session transaction, so a
        # failure partway through still leaves nothing written once the
        # caller rolls back.
        await session.delete(existing)
        await session.flush()
        await restaurants.persist_tree(restaurant)

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
