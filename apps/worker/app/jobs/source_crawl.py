"""Job: source crawling.

Fetches a restaurant's verified source URL, hashes/stores the result via
`infrastructure/crawler/`, persists the resulting SourceSnapshot (so the
reviewer workflow's Temporal Hash Polling has "what was fetched last
time" to compare against later — see database/repositories/
source_snapshot_repository.py), then enqueues a `collector_workflow`
job to actually structure and validate the crawled content.

Idempotency: keyed by `source_id` (the Source row this crawl is for) —
two crawl jobs for the same source shouldn't run concurrently; a
re-enqueue of an already-in-flight crawl for that source is skipped, not
duplicated.
"""

from typing import Any

from rq import get_current_job

from apps.worker.app.jobs.logging import log_completed, log_failed, log_skipped, log_started
from core.config.logging import configure_logging
from core.config.settings import get_settings
from database.session import get_sessionmaker
from infrastructure.queue.async_bridge import run_async
from infrastructure.queue.lock import JobAlreadyRunningError, RestaurantJobLock
from infrastructure.queue.queues import QUEUE_COLLECTOR_WORKFLOW, get_queue
from infrastructure.queue.redis_connection import get_redis_connection

JOB_TYPE = "source_crawl"


async def _run(*, restaurant_id: str, source_id: str, source_url: str) -> dict[str, Any]:
    import uuid

    from infrastructure.crawler.crawler_service import CrawlerService
    from infrastructure.crawler.domain_lock import extract_domain
    from database.repositories.source_snapshot_repository import SourceSnapshotRepository
    from workflows.collector_workflow.dependencies import default_storage_adapter

    settings = get_settings()
    session_factory = get_sessionmaker()

    storage = default_storage_adapter(settings)
    crawler = CrawlerService(
        verified_domain=extract_domain(source_url), storage=storage, settings=settings
    )
    snapshot, _metadata = await crawler.fetch_and_store(source_id=uuid.UUID(source_id), url=source_url)

    async with session_factory() as session:
        snapshots = SourceSnapshotRepository(session)
        stored = await snapshots.create(snapshot)
        await session.commit()

    queue = get_queue(QUEUE_COLLECTOR_WORKFLOW)
    from apps.worker.app.jobs.collector_workflow import run_collector_workflow

    enqueued = queue.enqueue(
        run_collector_workflow,
        restaurant_id=restaurant_id,
        source_id=source_id,
        source_snapshot_id=str(stored.id),
    )

    return {
        "source_snapshot_id": str(stored.id),
        "content_hash": stored.content_hash,
        "collector_workflow_job_id": enqueued.id,
    }


def run_source_crawl(*, restaurant_id: str, source_url: str, source_id: str | None = None) -> dict[str, Any]:
    """RQ entry point. `source_id` is optional at the call boundary
    (restaurant_ingestion currently always supplies it via the Source row
    SourceAuthorityService already persisted) but locking/dedup keys on
    `restaurant_id` — a restaurant should never have two crawl jobs in
    flight at once regardless of which source triggered them."""
    configure_logging(get_settings())
    job = get_current_job()
    job_id = job.id if job else "sync"

    log_started(job_id=job_id, job_type=JOB_TYPE, restaurant_id=restaurant_id, source_url=source_url)

    if source_id is None:
        log_failed(job_id=job_id, job_type=JOB_TYPE, restaurant_id=restaurant_id, error="no source_id supplied")
        raise ValueError("source_crawl job requires a source_id")

    try:
        with RestaurantJobLock(get_redis_connection(), restaurant_id=restaurant_id, job_type=JOB_TYPE):
            result = run_async(_run, restaurant_id=restaurant_id, source_id=source_id, source_url=source_url)
    except JobAlreadyRunningError as exc:
        log_skipped(job_id=job_id, job_type=JOB_TYPE, restaurant_id=restaurant_id, reason=str(exc))
        raise
    except Exception as exc:
        log_failed(job_id=job_id, job_type=JOB_TYPE, restaurant_id=restaurant_id, error=str(exc))
        raise

    log_completed(job_id=job_id, job_type=JOB_TYPE, restaurant_id=restaurant_id, **result)
    return result
