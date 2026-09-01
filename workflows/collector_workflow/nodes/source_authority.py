"""Source Authority node: resolves the restaurant's verified official
website via SourceAuthorityService and records the outcome on state.

Responsibilities (per the collector workflow's Agent 1 spec):
  - identify the official restaurant website (via SourceAuthorityService,
    never by inventing/guessing a URL itself)
  - reject aggregators (delegated to SourceAuthorityService, which already
    filters every candidate through the aggregator blocklist/domain
    validator before this node ever sees a URL)
  - persist the verified Source record (SourceAuthorityService does this
    for HIGH-confidence candidates; this node never writes a Source
    itself, so there is exactly one write path for that table)
  - produce structured output (SourceAuthorityResult) and update state
  - create the AgentRun record for this collector run
  - log failures (AuditService, AuditAction.AGENT_RUN_TRIGGER metadata)
  - never hallucinate a URL: NOT_FOUND/REJECTED/NEEDS_REVIEW all leave
    `source_url`/`source` unset on state rather than falling back to any
    guessed value

A LangGraph node function's signature is fixed to `(state) -> partial
state update`, so it can't take a DB session/provider as a parameter
directly. `build_source_authority_node` is a factory that closes over
those dependencies and returns the actual node callable — the pattern the
original placeholder's docstring called out for exactly this reason.
"""

import logging
import uuid
from typing import Any, Callable, Coroutine

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.services.audit_service import AuditService
from apps.api.app.services.source_authority_service import SourceAuthorityService
from core.schemas.agent_run import AgentWorkflowType
from core.schemas.audit import AuditAction, AuditEntityType
from core.schemas.source_authority import EntityResolutionQuery, ResolutionStatus
from database.repositories.agent_run_repository import AgentRunRepository
from database.repositories.source_repository import SourceRepository
from infrastructure.source_authority.provider import EntityResolutionProvider
from workflows.collector_workflow.state import CollectorState

logger = logging.getLogger("hungrx.workflows.collector.source_authority")

NODE_NAME = "source_authority"

SourceAuthorityNode = Callable[[CollectorState], Coroutine[Any, Any, dict[str, Any]]]


def _query_from_state(state: CollectorState) -> EntityResolutionQuery:
    restaurant = state.get("restaurant")
    if restaurant is None:
        raise ValueError("CollectorState.restaurant is required before the source_authority node runs")

    return EntityResolutionQuery(
        restaurant_id=restaurant.id,
        name=restaurant.name,
        city=restaurant.locations[0].city if restaurant.locations else None,
        state=restaurant.locations[0].state if restaurant.locations else None,
        country=restaurant.locations[0].country if restaurant.locations else None,
        phone=restaurant.locations[0].phone if restaurant.locations else None,
    )


def build_source_authority_node(
    session: AsyncSession, provider: EntityResolutionProvider
) -> SourceAuthorityNode:
    """Returns a node function bound to a live DB session and a concrete
    EntityResolutionProvider. The provider stays swappable — this factory
    doesn't care which implementation it's handed, per the interface
    boundary SourceAuthorityService already establishes."""

    service = SourceAuthorityService(session, provider)
    audit = AuditService(session)
    agent_runs = AgentRunRepository(session)

    async def source_authority_node(state: CollectorState) -> dict[str, Any]:
        try:
            query = _query_from_state(state)
        except ValueError as exc:
            logger.error("source_authority node: %s", exc)
            return {"errors": [{"node": NODE_NAME, "message": str(exc)}]}

        run = await agent_runs.create(
            workflow_type=AgentWorkflowType.COLLECTOR, restaurant_id=query.restaurant_id
        )

        result = await service.resolve_official_website(query)

        update: dict[str, Any] = {"agent_run_id": str(run.id)}

        if result.status == ResolutionStatus.VERIFIED:
            # Only a VERIFIED result — HIGH confidence, already persisted
            # by SourceAuthorityService — ever populates source_url/source
            # on state. NOT_FOUND, REJECTED, and NEEDS_REVIEW all fall
            # through to the error-logging branch below with nothing
            # written to those fields: a low-confidence guess is not a
            # verified source, and this node must never present one as if
            # it were.
            source_record = await SourceRepository(session).get_by_id(result.source_id)
            update["source_url"] = result.resolved_url
            update["source"] = source_record
            return update

        failure_message = (
            f"source_authority could not verify an official website for "
            f"restaurant_id={query.restaurant_id} (status={result.status.value}): "
            f"{result.reason or 'no reason given'}"
        )
        logger.warning(failure_message)

        await audit.log(
            action=AuditAction.AGENT_RUN_TRIGGER,
            entity_type=AuditEntityType.AGENT_RUN,
            entity_id=str(run.id),
            metadata={
                "node": NODE_NAME,
                "restaurant_id": str(query.restaurant_id),
                "status": result.status.value,
                "reason": result.reason,
                "rejected_candidates": result.rejected_candidates,
            },
        )
        await agent_runs.mark_failed(run.id, error_message=failure_message)

        update["errors"] = [{"node": NODE_NAME, "message": failure_message}]
        return update

    return source_authority_node
