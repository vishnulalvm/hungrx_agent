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

Staging -> approval -> transaction -> production -> audit log:
  - "staging" is the ProposedChange row (already APPROVED by the time
    this node runs).
  - "approval" is re-checked here (human_approval_status), not just
    trusted from graph routing.
  - "transaction": nothing in this node commits. RestaurantRepository.
    persist_tree only flushes, and every write below (production tree,
    ProposedChange status, audit row, AgentRun status) happens against
    the same caller-owned AsyncSession/transaction — so a failure
    anywhere in this node (including the re-validation below) leaves the
    session's pending changes to be rolled back by the caller rather
    than partially committed. See tests/unit/test_publish_node.py's
    TestRollsBackOnFailure for this proven against a real Postgres
    transaction.
  - Re-validation: `structured_json` may have been edited by a reviewer
    (edit_then_approve) after deterministic_validation last ran against
    it, so this node re-runs the same deterministic validate() the
    earlier node used, immediately before writing, and refuses to
    publish if it now finds any ERROR-severity issue — approval of
    *some* version of the data is not the same as approval of data that
    fails deterministic validation.
  - version/history: publish never mutates or deletes a prior
    ProposedChange/Approval row, so the full decision history for a
    restaurant survives every republish; this node does refuse to
    publish an entity_id that already has a PUBLISHED ProposedChange, so
    "publish" always represents a new entity, not a silent overwrite of
    an existing production restaurant (see TestPreventsRepublishing).
"""

import logging
import uuid
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.services.audit_service import AuditService
from core.schemas.audit import AuditAction, AuditEntityType
from core.schemas.proposed_change import ProposedChangeStatus
from core.schemas.restaurant import Restaurant
from core.validation.engine import validate
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

    async def _fail(state: CollectorState, message: str) -> dict[str, Any]:
        logger.error("publish node: %s", message)
        run_id = state.get("agent_run_id")
        if run_id is not None:
            await agent_runs.mark_failed(uuid.UUID(run_id), error_message=message)
        return {"errors": [{"node": NODE_NAME, "message": message}]}

    async def publish_node(state: CollectorState) -> dict[str, Any]:
        # Defense in depth: even though graph topology only routes here
        # on APPROVED, this node re-checks rather than trusting that it
        # was only ever reachable correctly — a routing bug elsewhere
        # must not turn into an unapproved production write.
        if state.get("human_approval_status") != ProposedChangeStatus.APPROVED:
            return await _fail(
                state, "publish node reached without human_approval_status == APPROVED; refusing to write"
            )

        structured_json = state.get("structured_json")
        proposed_change_id = state.get("proposed_change_id")
        if structured_json is None or proposed_change_id is None:
            return await _fail(
                state, "CollectorState.structured_json/proposed_change_id are required before publish runs"
            )

        # Re-validate immediately before commit: the data may have been
        # hand-edited by a reviewer (edit_then_approve) since
        # deterministic_validation last ran, so an approval is not
        # itself proof the current structured_json still passes.
        outcome = validate(structured_json)
        if not outcome.is_valid:
            return await _fail(
                state,
                "re-validation failed immediately before publish; refusing to write: "
                + "; ".join(f"{issue.field_path}: {issue.message}" for issue in outcome.errors),
            )

        restaurant = Restaurant.model_validate(structured_json)

        existing = await proposed_changes.get_published_for_entity(restaurant.id)
        if existing is not None and existing.id != uuid.UUID(proposed_change_id):
            return await _fail(
                state,
                f"restaurant {restaurant.id} already has a published change "
                f"({existing.id}); refusing to republish over it",
            )

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
