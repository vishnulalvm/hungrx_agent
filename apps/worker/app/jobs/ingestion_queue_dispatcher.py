"""Job: sequential dispatcher for the manual/verified restaurant ingestion
queue (database/models/ingestion_queue_item.py).

Requirement: restaurants in this queue must be processed strictly one at
a time — one full pipeline run (verify URL -> crawl -> collector
workflow, pausing at human_review or finishing at publish/failure) must
resolve before the next queued restaurant starts. RestaurantJobLock only
prevents overlap for the *same* restaurant; it does nothing to order
*different* restaurants, and the existing restaurant_ingestion ->
source_crawl -> collector_workflow chain is three separately enqueued RQ
jobs that could otherwise interleave freely with a second restaurant's
chain.

Design: this one job claims exactly one QUEUED item
(IngestionQueueRepository.claim_next_queued — atomic via `FOR UPDATE SKIP
LOCKED`, so a second concurrent dispatcher can never claim the same row)
and runs that restaurant's ENTIRE pipeline inline — calling the same
underlying async `_run` functions the separate
manual_restaurant_ingestion/source_crawl/collector_workflow jobs use,
sequentially awaited within one event loop — rather than enqueueing them
and returning. This makes "one restaurant at a time" true by
construction: the dispatcher job simply doesn't finish (and doesn't
self-enqueue) until that restaurant's run has reached human_review,
publish, or failure. The tradeoff (accepted deliberately, see the
project's implementation plan) is losing the separate per-stage RQ job
entries/observability the search-based chain has; this queue's own
status column is the source of truth for a manually-ingested restaurant's
progress instead.

A single Redis lock (not restaurant-scoped) guards against two upload
batches both trying to start a dispatch chain concurrently — held for
one claim-and-run cycle, released just before this job's self-enqueue so
the next cycle (potentially on a different worker instance) can
re-acquire cleanly.
"""

import uuid
from typing import Any

import redis

from apps.worker.app.jobs.logging import log_completed, log_failed, log_skipped, log_started
from core.config.logging import configure_logging
from core.config.settings import get_settings
from database.session import get_sessionmaker
from infrastructure.queue.queues import QUEUE_INGESTION_QUEUE_DISPATCH, get_queue
from infrastructure.queue.redis_connection import get_redis_connection

JOB_TYPE = "ingestion_queue_dispatch"

_DISPATCH_LOCK_KEY = "hungrx:ingestion-queue:dispatch"
# No TTL: a manual ingestion's full pipeline can legitimately sit paused
# at human_review for minutes to days (requirement: strictly one at a
# time, however long that takes), so a time-based expiry could let a
# second chain start while the first is still genuinely in progress.
# Released explicitly by _DispatchLock.__exit__ instead (token-checked,
# same pattern as infrastructure/queue/lock.py's RestaurantJobLock).


class _DispatchAlreadyRunningError(Exception):
    pass


class _DispatchLock:
    def __init__(self, connection: redis.Redis) -> None:
        self._connection = connection
        self._token = str(uuid.uuid4())

    def __enter__(self) -> "_DispatchLock":
        acquired = self._connection.set(_DISPATCH_LOCK_KEY, self._token, nx=True)
        if not acquired:
            raise _DispatchAlreadyRunningError()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        release_script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """
        self._connection.eval(release_script, 1, _DISPATCH_LOCK_KEY, self._token)


def try_trigger_dispatch() -> bool:
    """Enqueues the dispatcher job iff no dispatch chain is currently
    running. Called after a bulk upload and after a manual requeue — if a
    chain is already running, the newly-(re)queued row will be picked up
    automatically by that chain's own self-enqueue once it finishes its
    current item, so returning False here isn't an error."""
    connection = get_redis_connection()
    if connection.exists(_DISPATCH_LOCK_KEY):
        return False

    get_queue(QUEUE_INGESTION_QUEUE_DISPATCH).enqueue(run_dispatch_next_ingestion_queue_item)
    return True


async def _run_one_item(item_id: uuid.UUID) -> dict[str, Any]:
    # Deliberately calling _verify_only / _crawl_and_store — the
    # enqueue-only variants of these two stages' full _run — not the
    # full _run functions themselves. manual_restaurant_ingestion._run
    # and source_crawl._run each end by enqueueing the NEXT stage as a
    # separate RQ job (the right behavior for their standalone 3-hop-chain
    # callers), which here would race a second, independently-scheduled
    # copy of this same restaurant's pipeline against the one this
    # function already drives inline — and that second copy would be
    # missing restaurant_name/menu_url entirely, since those only
    # exist as this function's own local variables, not on anything a
    # separately enqueued job receives.
    from apps.worker.app.jobs.collector_workflow import _run as run_collector_workflow_inline
    from apps.worker.app.jobs.manual_restaurant_ingestion import _verify_only as verify_source_inline
    from apps.worker.app.jobs.source_crawl import _crawl_and_store as crawl_and_store_inline
    from database.repositories.ingestion_queue_repository import IngestionQueueRepository

    session_factory = get_sessionmaker()

    async with session_factory() as session:
        item = await IngestionQueueRepository(session).get_by_id(item_id)
        if item is None:
            raise ValueError(f"ingestion queue item {item_id} not found")
        name, menu_url, nutrition_url = item.name, item.menu_url, item.nutrition_url
        await session.commit()

    restaurant_seed_id = str(uuid.uuid4())
    async with session_factory() as session:
        await IngestionQueueRepository(session).set_restaurant_seed_id(
            item_id, restaurant_seed_id=restaurant_seed_id
        )
        await session.commit()

    ingestion_outcome = await verify_source_inline(
        restaurant_seed_id=restaurant_seed_id, menu_url=menu_url
    )
    if ingestion_outcome["status"] != "verified":
        raise RuntimeError(f"source verification rejected: {ingestion_outcome.get('reason')}")

    restaurant_id = ingestion_outcome["restaurant_id"]
    source_id = ingestion_outcome["source_id"]
    source_url = ingestion_outcome["source_url"]

    crawl_outcome = await crawl_and_store_inline(source_id=source_id, source_url=source_url)

    collector_outcome = await run_collector_workflow_inline(
        restaurant_id=restaurant_id,
        restaurant_name=name,
        menu_url=menu_url,
        nutrition_url=nutrition_url,
    )

    return {
        "restaurant_id": restaurant_id,
        "source_id": source_id,
        "source_snapshot_id": crawl_outcome["source_snapshot_id"],
        "agent_run_id": collector_outcome.get("agent_run_id"),
        "published_restaurant_id": collector_outcome.get("published_restaurant_id"),
        "errors": collector_outcome.get("errors", []),
    }


def run_dispatch_next_ingestion_queue_item() -> dict[str, Any]:
    configure_logging(get_settings())

    connection = get_redis_connection()
    try:
        with _DispatchLock(connection):
            return _dispatch_one_cycle()
    except _DispatchAlreadyRunningError:
        log_skipped(
            job_id="inline",
            job_type=JOB_TYPE,
            restaurant_id=None,
            reason="a dispatch chain is already running",
        )
        return {"status": "skipped"}


def _dispatch_one_cycle() -> dict[str, Any]:
    import asyncio

    return asyncio.run(_dispatch_one_cycle_async())


async def _dispatch_one_cycle_async() -> dict[str, Any]:
    from database.repositories.ingestion_queue_repository import IngestionQueueRepository

    session_factory = get_sessionmaker()

    async with session_factory() as session:
        claimed = await IngestionQueueRepository(session).claim_next_queued()
        await session.commit()
        item_id = claimed.id if claimed is not None else None

    if item_id is None:
        return {"status": "empty"}

    log_started(job_id="inline", job_type=JOB_TYPE, restaurant_id=None, ingestion_queue_item_id=str(item_id))

    try:
        result = await _run_one_item(item_id)
        # The collector graph reports a node failure (e.g.
        # deterministic_validation finding no menus/dishes — see
        # workflows/collector_workflow/graph.py's _route_if_no_errors) by
        # returning {"errors": [...]} on its result rather than raising —
        # that's what lets the graph route straight to END with the
        # AgentRun already marked FAILED, instead of raising out of
        # ainvoke(). _run_one_item propagates that list through
        # unchanged, so it must be checked here explicitly: without this,
        # a run that produced no usable data would still mark the queue
        # item SUCCEEDED just because no Python exception was raised.
        node_errors = result.get("errors") or []
        if node_errors:
            raise RuntimeError("; ".join(err.get("message", str(err)) for err in node_errors))
    except Exception as exc:
        async with session_factory() as session:
            await IngestionQueueRepository(session).mark_failed(item_id, error_message=str(exc))
            await session.commit()
        log_failed(job_id="inline", job_type=JOB_TYPE, restaurant_id=None, error=str(exc))
        result = {"status": "failed", "error": str(exc)}
    else:
        async with session_factory() as session:
            await IngestionQueueRepository(session).mark_succeeded(item_id)
            await session.commit()
        log_completed(job_id="inline", job_type=JOB_TYPE, **result)
        result = {"status": "succeeded", **result}

    # Whether this item succeeded or failed, its own claim already told us
    # a QUEUED row existed a moment ago — re-enqueue this same job
    # (outside the dispatch lock, which __exit__ releases right after this
    # function returns) so the next QUEUED row (if any) gets picked up.
    # If the queue is empty on that next cycle's own claim, it returns
    # {"status": "empty"} without a further self-enqueue, so the chain
    # naturally stops rather than busy-polling forever.
    get_queue(QUEUE_INGESTION_QUEUE_DISPATCH).enqueue(run_dispatch_next_ingestion_queue_item)

    return result
