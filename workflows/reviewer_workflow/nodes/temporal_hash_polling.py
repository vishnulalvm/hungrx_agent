"""Temporal Hash Polling node (Reviewer Workflow, stage 1): re-fetches a
restaurant's verified source and compares its content hash against the
last snapshot recorded for that source. This is the workflow's early-out
gate — everything downstream (re-extraction, diffing, an AI call, a
human review interrupt) only makes sense to run when the source has
actually changed since last time, so this node alone decides whether the
run does any of that work at all.

Responsibilities:
  - look up state["source"] (the restaurant's verified Source — this
    node never resolves/guesses one itself, same "never hallucinate a
    URL" boundary the collector workflow's source_authority node
    enforces)
  - fetch the source's root page fresh (HTTP-only; no browser fallback
    here — this node only needs the raw bytes to hash, not to run link
    discovery or interpret content)
  - hash the fresh fetch (SHA-256, via infrastructure.crawler.hashing —
    the same hashing SnapshotService uses for the collector workflow, so
    a hash computed here is directly comparable to one computed there)
  - compare against SourceSnapshotRepository.get_latest_for_source's
    result: no prior snapshot -> treat as changed (first poll ever);
    otherwise hash equality decides `hash_changed`
  - persist the freshly fetched snapshot regardless of the outcome, so
    "what did we last see" always reflects the most recent poll, not
    just the most recent *change*
  - create an AgentRun (workflow_type=REVIEWER) at the start of every
    invocation, same bookkeeping pattern as the collector workflow's
    source_authority node

Graph routing (see graph.py's `_route_after_hash_polling`) sends the run
straight to END when `hash_changed` is False — an unchanged source never
reaches Targeted Re-Extraction, JSON Delta Generation, Delta Validation,
or Human Final Sync. When state has no `source_snapshot_id_content_type`
constraint we simply compare the most recent HTML root-page snapshot,
since that is what this node itself fetches.
"""

import logging
import uuid
from typing import Any, Awaitable, Callable, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.services.audit_service import AuditService
from core.config.settings import Settings
from core.schemas.agent_run import AgentWorkflowType
from core.schemas.audit import AuditAction, AuditEntityType
from core.schemas.source import Source, SourceSnapshot
from database.repositories.agent_run_repository import AgentRunRepository
from database.repositories.source_snapshot_repository import SourceSnapshotRepository
from infrastructure.crawler.crawler_service import CrawlerService
from infrastructure.storage.base import StorageAdapter
from workflows.reviewer_workflow.state import ReviewerState

logger = logging.getLogger("hungrx.workflows.reviewer.temporal_hash_polling")

NODE_NAME = "temporal_hash_polling"

TemporalHashPollingNode = Callable[[ReviewerState], Awaitable[dict[str, Any]]]


class RootPageFetcher(Protocol):
    """Seam between this node's own logic (hash comparison, persistence,
    routing decision) and the actual network fetch — production uses
    `CrawlerServiceRootPageFetcher`; tests use a fake, same pattern as
    workflows/collector_workflow/nodes/extraction.py's PageFetcher."""

    async def fetch_root(self, *, source_id: uuid.UUID, url: str) -> SourceSnapshot: ...


class CrawlerServiceRootPageFetcher:
    def __init__(self, *, verified_domain: str, storage: StorageAdapter, settings: Settings) -> None:
        self._crawler = CrawlerService(verified_domain=verified_domain, storage=storage, settings=settings)

    async def fetch_root(self, *, source_id: uuid.UUID, url: str) -> SourceSnapshot:
        snapshot, _metadata = await self._crawler.fetch_and_store(source_id=source_id, url=url)
        return snapshot


def build_temporal_hash_polling_node(
    session: AsyncSession,
    storage: StorageAdapter,
    settings: Settings,
    *,
    fetcher_factory: Callable[[str], RootPageFetcher] | None = None,
) -> TemporalHashPollingNode:
    """`fetcher_factory(verified_domain) -> RootPageFetcher` defaults to
    a real CrawlerService-backed fetcher; pass a fake factory in tests to
    avoid any real network call while still exercising this node's own
    hash-comparison/persistence/routing logic."""
    audit = AuditService(session)
    agent_runs = AgentRunRepository(session)
    snapshots = SourceSnapshotRepository(session)
    factory = fetcher_factory or (
        lambda domain: CrawlerServiceRootPageFetcher(verified_domain=domain, storage=storage, settings=settings)
    )

    async def temporal_hash_polling_node(state: ReviewerState) -> dict[str, Any]:
        source: Source | None = state.get("source")
        restaurant = state.get("restaurant")

        if source is None or restaurant is None:
            message = (
                "ReviewerState.source/restaurant are required before temporal_hash_polling runs "
                "(the caller must load the restaurant's verified Source before starting a review run)"
            )
            logger.error("temporal_hash_polling node: %s", message)
            return {"errors": [{"node": NODE_NAME, "message": message}]}

        run = await agent_runs.create(workflow_type=AgentWorkflowType.REVIEWER, restaurant_id=restaurant.id)
        update: dict[str, Any] = {"agent_run_id": str(run.id)}

        previous = await snapshots.get_latest_for_source(source.id)
        previous_hash = previous.content_hash if previous is not None else None

        fetcher = factory(source.url)
        try:
            fresh_snapshot = await fetcher.fetch_root(source_id=source.id, url=source.url)
        except Exception as exc:  # crawler/network failures must not crash the graph run
            failure_message = f"temporal_hash_polling failed to re-fetch source_id={source.id}: {exc}"
            logger.warning(failure_message)
            await audit.log(
                action=AuditAction.AGENT_RUN_TRIGGER,
                entity_type=AuditEntityType.AGENT_RUN,
                entity_id=str(run.id),
                metadata={"node": NODE_NAME, "source_id": str(source.id), "error": str(exc)},
            )
            await agent_runs.mark_failed(run.id, error_message=failure_message)
            update["errors"] = [{"node": NODE_NAME, "message": failure_message}]
            return update

        await snapshots.create(fresh_snapshot)

        hash_changed = previous_hash is None or previous_hash != fresh_snapshot.content_hash

        update.update(
            {
                "previous_content_hash": previous_hash,
                "current_content_hash": fresh_snapshot.content_hash,
                "hash_changed": hash_changed,
                "polled_snapshot": fresh_snapshot,
            }
        )

        if not hash_changed:
            # Not a failure — logged as an audit-visible "nothing to do"
            # outcome, and the AgentRun still completes successfully;
            # graph routing (not an error) is what stops the run here.
            await audit.log(
                action=AuditAction.AGENT_RUN_TRIGGER,
                entity_type=AuditEntityType.AGENT_RUN,
                entity_id=str(run.id),
                metadata={
                    "node": NODE_NAME,
                    "source_id": str(source.id),
                    "outcome": "unchanged",
                    "content_hash": fresh_snapshot.content_hash,
                },
            )
            await agent_runs.mark_succeeded(run.id)

        return update

    return temporal_hash_polling_node
