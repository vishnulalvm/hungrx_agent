"""Human Review node (Collector Workflow Agent 5): creates a
ProposedChange record from the validated structured_json, then pauses
the graph via LangGraph's `interrupt()` until an admin API endpoint
resumes it with a decision (approve / reject / edit_then_approve).

This is the graph's human-in-the-loop boundary. The pipeline up to here
(source_authority -> extraction -> multimodal_translation ->
deterministic_validation) is fully automated; nothing past this node
runs — in particular, nothing is ever written to the production
restaurant/menu/dish tables (see database/repositories/
restaurant_repository.py and workflows/collector_workflow/nodes/
publish.py) — until a real human decision comes back through `resume`.

Flow:
  1. First entry: create a ProposedChange row (status PENDING) recording
     the full proposed Restaurant tree, audit PROPOSED_CHANGE_CREATE,
     then call `interrupt(payload)`. LangGraph suspends execution here —
     the coroutine does not return; the surrounding graph.ainvoke/astream
     call returns to its caller with an `__interrupt__` entry instead of
     a completed result.
  2. Resume (an admin API endpoint calls
     graph.ainvoke(Command(resume=decision), config=...) against the same
     thread_id): LangGraph replays this node function from the top with
     the *same input state as before the interrupt* — `structured_json`/
     `restaurant` are there, but nothing this node itself would have
     returned is, since that return never happened. `interrupt(payload)`
     on this replay immediately returns the queued resume value instead
     of pausing again.

Idempotent record creation (the part that's easy to get wrong): because
step 2 replays this node from the top, a naive "have I already created a
ProposedChange" check against `state` would always see `None` and create
a *second* row on every resume, racing right past the interrupt(). This
node instead looks the record up by `thread_id` (== `agent_run_id`,
required precisely because it doubles as the LangGraph checkpoint thread
identity) — `get_by_thread_id` returning a record is what "already
created" actually means here, not anything carried on state.

The actual DB mutation the admin's decision represents (ProposedChange
status, an Approval record, the audit log for the decision itself)
happens in the admin API endpoint that calls resume — not here — so it's
visible to the API response synchronously, in the same request/
transaction as the decision. This node only reads back
`state["human_review_decision"]` (set onto state by the admin endpoint's
`Command(resume=..., update=...)` before resuming) to decide how to
route.
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
from workflows.collector_workflow.state import CollectorState

logger = logging.getLogger("hungrx.workflows.collector.human_review")

NODE_NAME = "human_review"

HumanReviewNode = Callable[[CollectorState], Awaitable[dict[str, Any]]]


def build_human_review_node(session: AsyncSession) -> HumanReviewNode:
    audit = AuditService(session)
    proposed_changes = ProposedChangeRepository(session)

    async def human_review_node(state: CollectorState) -> dict[str, Any]:
        structured_json = state.get("structured_json")
        validation_result = state.get("validation_result")
        restaurant = state.get("restaurant")
        agent_run_id = state.get("agent_run_id")

        if structured_json is None or restaurant is None or not agent_run_id:
            message = (
                "CollectorState.structured_json/restaurant/agent_run_id are required before "
                "the human_review node runs (deterministic_validation must succeed first, and "
                "agent_run_id doubles as the checkpoint thread identity this node depends on)"
            )
            logger.error("human_review node: %s", message)
            return {"errors": [{"node": NODE_NAME, "message": message}]}

        record = await proposed_changes.get_by_thread_id(agent_run_id)

        if record is None:
            record = await proposed_changes.create(
                entity_type=ProposedChangeEntityType.RESTAURANT,
                entity_id=restaurant.id,
                structured_json=structured_json,
                validation_result=validation_result or {},
                agent_run_id=_safe_uuid(agent_run_id),
                source_id=state["source"].id if state.get("source") else None,
                thread_id=agent_run_id,
            )
            await audit.log(
                action=AuditAction.PROPOSED_CHANGE_CREATE,
                entity_type=AuditEntityType.PROPOSED_CHANGE,
                entity_id=str(record.id),
                metadata={"node": NODE_NAME, "restaurant_id": str(restaurant.id)},
            )

        review_task = {
            "proposed_change_id": str(record.id),
            "restaurant_id": str(restaurant.id),
            "restaurant_name": restaurant.name,
            "is_valid": (validation_result or {}).get("is_valid"),
            "issue_count": len((validation_result or {}).get("issues", [])),
        }
        # On first entry this call raises internally and never returns —
        # LangGraph suspends the run right here. On a resumed replay it
        # returns the resume value the admin endpoint supplied instead.
        decision = interrupt(review_task)

        return _apply_decision(decision, record=record)

    return human_review_node


def _safe_uuid(value: str):
    import uuid

    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def _apply_decision(decision: Any, *, record: ProposedChange) -> dict[str, Any]:
    if not isinstance(decision, dict) or not decision:
        return {
            "errors": [
                {"node": NODE_NAME, "message": "human_review resumed with no usable decision"}
            ]
        }

    action = decision.get("action")
    update: dict[str, Any] = {"proposed_change_id": str(record.id)}

    if action == "approve":
        update["human_approval_status"] = ProposedChangeStatus.APPROVED
    elif action == "edit_then_approve":
        update["human_approval_status"] = ProposedChangeStatus.APPROVED
        if decision.get("edited_structured_json") is not None:
            update["structured_json"] = decision["edited_structured_json"]
    elif action == "reject":
        update["human_approval_status"] = ProposedChangeStatus.REJECTED
    else:
        return {
            "errors": [
                {"node": NODE_NAME, "message": f"human_review received an unrecognized decision action: {action!r}"}
            ]
        }

    return update
