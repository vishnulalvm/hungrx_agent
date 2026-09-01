"""Job: initial restaurant ingestion.

Entry point for onboarding a brand-new restaurant: given a name +
location (no restaurant row exists yet — the production `restaurants`
table is only ever written by a collector workflow's publish node), runs
Source Authority resolution to find/verify the restaurant's official
website. On VERIFIED, enqueues a `source_crawl` job for that source. On
anything else (NOT_FOUND/REJECTED/NEEDS_REVIEW), the run ends here —
there is nothing to crawl without a verified domain, and NEEDS_REVIEW
already means a human has to weigh in before any automated crawling of
an unconfirmed candidate happens.

Idempotency: keyed by `restaurant_seed_id` (a caller-supplied stable id
for "this ingestion request," e.g. a request UUID from whatever created
it — since no restaurant row exists yet, restaurant_id itself doesn't
exist to dedup on). Deduping/locking is on that seed id.
"""

import uuid
from typing import Any

from rq import get_current_job

from apps.worker.app.jobs.logging import log_completed, log_failed, log_skipped, log_started
from core.config.logging import configure_logging
from core.config.settings import get_settings
from core.schemas.source_authority import EntityResolutionQuery, ResolutionStatus
from database.session import get_sessionmaker
from infrastructure.queue.lock import JobAlreadyRunningError, RestaurantJobLock
from infrastructure.queue.queues import QUEUE_SOURCE_CRAWL, get_queue
from infrastructure.queue.redis_connection import get_redis_connection
from infrastructure.source_authority.null_provider import NullEntityResolutionProvider

JOB_TYPE = "restaurant_ingestion"


async def _run(
    *,
    restaurant_seed_id: str,
    name: str,
    city: str | None,
    state: str | None,
    country: str | None,
    phone: str | None,
) -> dict[str, Any]:
    from apps.api.app.services.source_authority_service import SourceAuthorityService

    settings = get_settings()
    session_factory = get_sessionmaker()

    async with session_factory() as session:
        # No real EntityResolutionProvider is wired up process-wide yet
        # (same NullEntityResolutionProvider default the collector
        # workflow's graph.py falls back to) — this job's own
        # responsibility ends at "resolve via whatever provider is
        # configured," not at supplying one.
        provider = NullEntityResolutionProvider()
        service = SourceAuthorityService(session, provider)

        restaurant_id = uuid.uuid5(uuid.NAMESPACE_URL, f"hungrx:restaurant-seed:{restaurant_seed_id}")
        query = EntityResolutionQuery(
            restaurant_id=restaurant_id, name=name, city=city, state=state, country=country, phone=phone
        )
        result = await service.resolve_official_website(query)
        await session.commit()

    outcome = {"status": result.status.value, "restaurant_id": str(restaurant_id)}

    if result.status != ResolutionStatus.VERIFIED or result.resolved_url is None:
        return outcome

    queue = get_queue(QUEUE_SOURCE_CRAWL)
    from apps.worker.app.jobs.source_crawl import run_source_crawl

    enqueued = queue.enqueue(
        run_source_crawl,
        restaurant_id=str(restaurant_id),
        source_url=result.resolved_url,
    )
    outcome["source_crawl_job_id"] = enqueued.id
    return outcome


def run_restaurant_ingestion(
    *,
    restaurant_seed_id: str,
    name: str,
    city: str | None = None,
    state: str | None = None,
    country: str | None = None,
    phone: str | None = None,
) -> dict[str, Any]:
    """RQ entry point. `restaurant_seed_id` is the dedup key — the
    caller (an API endpoint accepting a new-restaurant request, most
    likely) is responsible for supplying a stable id for "this specific
    ingestion request" so a retried/duplicated enqueue doesn't kick off
    two concurrent resolutions for the same restaurant."""
    configure_logging(get_settings())
    job = get_current_job()
    job_id = job.id if job else "sync"

    log_started(job_id=job_id, job_type=JOB_TYPE, restaurant_id=None, restaurant_seed_id=restaurant_seed_id)

    try:
        with RestaurantJobLock(get_redis_connection(), restaurant_id=restaurant_seed_id, job_type=JOB_TYPE):
            from infrastructure.queue.async_bridge import run_async

            result = run_async(
                _run,
                restaurant_seed_id=restaurant_seed_id,
                name=name,
                city=city,
                state=state,
                country=country,
                phone=phone,
            )
    except JobAlreadyRunningError as exc:
        log_skipped(job_id=job_id, job_type=JOB_TYPE, restaurant_id=None, reason=str(exc))
        raise
    except Exception as exc:
        log_failed(job_id=job_id, job_type=JOB_TYPE, restaurant_id=None, error=str(exc))
        raise

    log_completed(job_id=job_id, job_type=JOB_TYPE, **result)
    return result
