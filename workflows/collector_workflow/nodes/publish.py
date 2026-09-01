"""Publish node (Collector Workflow Agent 6): applies an approved
ProposedChange to the live restaurant/menu/dish production tables and
marks it ProposedChangeStatus.PUBLISHED. The terminal node of a
successful collector run.

This is the only place in the entire codebase that writes to
database.models.restaurant's tables — it does so through
RestaurantRepository, which is itself imported nowhere else (see that
module's docstring). Combined with the graph's routing
(workflows/collector_workflow/graph.py's `_route_after_human_review`
only sends a run here when `human_approval_status ==
ProposedChangeStatus.APPROVED`, which — per human_review.py — is only
ever set from a real resumed admin decision, never a default), this
publish node is structurally unreachable for anything that hasn't gone
through and passed human review. There is no code path where
unapproved/pending data reaches these tables.
"""

import logging
import uuid
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.services.audit_service import AuditService
from core.schemas.audit import AuditAction, AuditEntityType
from core.schemas.proposed_change import ProposedChangeStatus
from core.schemas.restaurant import Restaurant
from database.repositories.agent_run_repository import AgentRunRepository
from database.repositories.proposed_change_repository import ProposedChangeRepository
from database.repositories.restaurant_repository import RestaurantRepository
from workflows.collector_workflow.state import CollectorState

logger = logging.getLogger("hungrx.workflows.collector.publish")

NODE_NAME = "publish"

PublishNode = Callable[[CollectorState], Awaitable[dict[str, Any]]]


def build_publish_node(session: AsyncSession) -> PublishNode:
    audit = AuditService(session)
    proposed_changes = ProposedChangeRepository(session)
    agent_runs = AgentRunRepository(session)
    restaurants = RestaurantRepository(session)

    async def publish_node(state: CollectorState) -> dict[str, Any]:
        # Defense in depth: even though graph topology only routes here
        # on APPROVED, this node re-checks rather than trusting that it
        # was only ever reachable correctly — a routing bug elsewhere
        # must not turn into an unapproved production write.
        if state.get("human_approval_status") != ProposedChangeStatus.APPROVED:
            message = "publish node reached without human_approval_status == APPROVED; refusing to write"
            logger.error(message)
            return {"errors": [{"node": NODE_NAME, "message": message}]}

        structured_json = state.get("structured_json")
        proposed_change_id = state.get("proposed_change_id")
        if structured_json is None or proposed_change_id is None:
            message = "CollectorState.structured_json/proposed_change_id are required before publish runs"
            logger.error("publish node: %s", message)
            return {"errors": [{"node": NODE_NAME, "message": message}]}

        restaurant = Restaurant.model_validate(structured_json)
        await restaurants.persist_tree(restaurant)

        await proposed_changes.update_status(
            uuid.UUID(proposed_change_id), status=ProposedChangeStatus.PUBLISHED
        )

        await audit.log(
            action=AuditAction.PROPOSED_CHANGE_PUBLISH,
            entity_type=AuditEntityType.PROPOSED_CHANGE,
            entity_id=proposed_change_id,
            metadata={"node": NODE_NAME, "restaurant_id": str(restaurant.id)},
        )

        run_id = state.get("agent_run_id")
        if run_id is not None:
            await agent_runs.mark_succeeded(uuid.UUID(run_id))

        return {"published_restaurant_id": str(restaurant.id)}

    return publish_node
