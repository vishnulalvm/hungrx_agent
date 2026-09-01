"""Temporal Hash Polling node (Reviewer Workflow Agent 6: Change
Detection): re-fetches a restaurant's active verified source and
compares its content hash against the last recorded snapshot for that
source. This is the workflow's early-out gate — everything downstream
(re-extraction, diffing, an AI call, a human review interrupt) only
makes sense to run when the source has actually changed since last
time, so this node alone decides whether the run does any of that work
at all.

Responsibilities (per the reviewer workflow's Agent 6 spec):
  - load the active source URL: looked up fresh via
    SourceRepository.get_verified_website_for_restaurant, never trusted
    blindly off whatever happens to be on state — a caller may pass
    state["source"] as a hint/fallback (e.g. a caller that already
    resolved it in the same request), but the authoritative check is
    always "what does the database say is this restaurant's current
    verified website right now." Same "never hallucinate a URL"
    precedent as the collector workflow's source_authority node, applied
    here as "never trust a stale/caller-supplied source blindly."
  - fetch the current source: HTTP-only, root page only (no browser
    fallback, no link discovery) — this node only needs the raw bytes to
    hash, not to interpret content. This, plus doing no AI call at all,
    is what "minimum compute / minimum LLM usage" means concretely: a
    single lightweight HTTP GET is the entire cost of a poll that finds
    nothing changed.
  - calculate SHA-256 hash of the fresh fetch (infrastructure.crawler.
    hashing — the same hashing SnapshotService uses for the collector
    workflow, so a hash computed here is directly comparable)
  - compare with previous snapshot: SourceSnapshotRepository.
    get_latest_for_source's result decides it — no prior snapshot at all
    (first-ever poll) is always treated as changed; otherwise hash
    equality decides `hash_changed`
  - terminate workflow when unchanged / continue when changed: this node
    only sets `hash_changed` on state — graph.py's
    `_route_after_hash_polling` conditional edge is what actually routes
    to END vs. the next node, so the terminate/continue decision is
    graph topology, not something this node does by raising or
    short-circuiting itself
  - persist the new snapshot regardless of outcome, so "what did we last
    see" always reflects the most recent poll, not just the most recent
    *change*
  - record agent run metrics: fetch duration (ms), response byte count,
    and the changed/unchanged outcome are written onto
    AgentRun.metrics via AgentRunRepository.update_metrics — a durable,
    queryable record of this run's own performance/outcome data,
    distinct from the audit log's discrete event record (also still
    written, for the human-readable audit trail)
  - create an AgentRun (workflow_type=REVIEWER) at the start of every
    invocation, same bookkeeping pattern as the collector workflow's
    source_authority node
"""

import logging
import time
import uuid
from typing import Any, Awaitable, Callable, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.services.audit_service import AuditService
from core.config.settings import Settings
from core.schemas.agent_run import AgentWorkflowType
from core.schemas.audit import AuditAction, AuditEntityType
from core.schemas.source import Source, SourceSnapshot
from database.repositories.agent_run_repository import AgentRunRepository
from database.repositories.source_repository import SourceRepository
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
    sources = SourceRepository(session)
    factory = fetcher_factory or (
        lambda domain: CrawlerServiceRootPageFetcher(verified_domain=domain, storage=storage, settings=settings)
    )

    async def temporal_hash_polling_node(state: ReviewerState) -> dict[str, Any]:
        restaurant = state.get("restaurant")

        if restaurant is None:
            message = "ReviewerState.restaurant is required before temporal_hash_polling runs"
            logger.error("temporal_hash_polling node: %s", message)
            return {"errors": [{"node": NODE_NAME, "message": message}]}

        # Load the active source URL fresh from the database rather than
        # trusting state["source"] blindly — a caller-supplied hint could
        # be stale (a different website was verified since); the
        # database is the single authoritative answer to "what is this
        # restaurant's current verified website."
        source_row = await sources.get_verified_website_for_restaurant(restaurant.id)
        if source_row is not None:
            source: Source | None = Source(
                id=source_row.id,
                restaurant_id=source_row.restaurant_id,
                source_type=source_row.source_type,
                url=source_row.url,
                is_verified_domain=source_row.is_verified_domain,
            )
        else:
            fallback = state.get("source")
            source = fallback if fallback is not None and fallback.is_verified_domain else None

        if source is None:
            message = f"no verified active source found for restaurant_id={restaurant.id}"
            logger.error("temporal_hash_polling node: %s", message)
            return {"errors": [{"node": NODE_NAME, "message": message}]}

        run = await agent_runs.create(workflow_type=AgentWorkflowType.REVIEWER, restaurant_id=restaurant.id)
        update: dict[str, Any] = {"agent_run_id": str(run.id), "source": source}

        previous = await snapshots.get_latest_for_source(source.id)
        previous_hash = previous.content_hash if previous is not None else None

        fetcher = factory(source.url)
        fetch_started_at = time.monotonic()
        try:
            fresh_snapshot = await fetcher.fetch_root(source_id=source.id, url=source.url)
        except Exception as exc:  # crawler/network failures must not crash the graph run
            fetch_duration_ms = round((time.monotonic() - fetch_started_at) * 1000, 2)
            failure_message = f"temporal_hash_polling failed to re-fetch source_id={source.id}: {exc}"
            logger.warning(failure_message)
            await audit.log(
                action=AuditAction.AGENT_RUN_TRIGGER,
                entity_type=AuditEntityType.AGENT_RUN,
                entity_id=str(run.id),
                metadata={"node": NODE_NAME, "source_id": str(source.id), "error": str(exc)},
            )
            await agent_runs.update_metrics(
                run.id,
                metrics={
                    "node": NODE_NAME,
                    "fetch_duration_ms": fetch_duration_ms,
                    "outcome": "fetch_failed",
                },
            )
            await agent_runs.mark_failed(run.id, error_message=failure_message)
            update["errors"] = [{"node": NODE_NAME, "message": failure_message}]
            return update
        fetch_duration_ms = round((time.monotonic() - fetch_started_at) * 1000, 2)

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

        await agent_runs.update_metrics(
            run.id,
            metrics={
                "node": NODE_NAME,
                "fetch_duration_ms": fetch_duration_ms,
                "content_length_bytes": fresh_snapshot.content_length_bytes,
                "hash_changed": hash_changed,
                "outcome": "changed" if hash_changed else "unchanged",
            },
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
