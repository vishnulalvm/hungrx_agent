"""Job: manual restaurant ingestion (verified URL supplied by an admin,
no Source Authority search).

Sibling to apps/worker/app/jobs/restaurant_ingestion.py, not a
modification of it — that job's whole purpose is search-based resolution
via SourceAuthorityService; this one skips search entirely and instead
validates+persists the admin-supplied menu_url directly (see
apps/api/app/services/manual_source_verification.py). On success,
enqueues a source_crawl job exactly like restaurant_ingestion.py does —
that job and everything downstream of it are reused unmodified.

Not used directly by apps/api/app/routers/v1/admin/router.py's bulk
upload endpoint — that only inserts IngestionQueueItem rows.
apps/worker/app/jobs/ingestion_queue_dispatcher.py is what actually calls
this job's `_verify_only` (not `_run` — see that function's docstring for
why the enqueue side effect must be skipped) inline, not via RQ enqueue,
as the first stage of one queue item's pipeline, so that the whole chain
for one restaurant can be awaited synchronously within a single
dispatcher job — see that module's docstring for why.
"""

import uuid
from typing import Any

from apps.worker.app.jobs.logging import log_completed, log_failed, log_skipped, log_started
from core.config.logging import configure_logging
from core.config.settings import get_settings
from database.session import get_sessionmaker
from infrastructure.queue.lock import JobAlreadyRunningError, RestaurantJobLock
from infrastructure.queue.redis_connection import get_redis_connection

JOB_TYPE = "restaurant_ingestion"


async def _verify_only(*, restaurant_seed_id: str, menu_url: str) -> dict[str, Any]:
    """The verification-only half of this job — validate+persist the
    Source, no side effect of enqueueing anything further. Factored out
    so apps/worker/app/jobs/ingestion_queue_dispatcher.py can call just
    this part inline without also triggering this job's own `_run`'s
    enqueue of a *second*, separately-scheduled source_crawl RQ job for
    the same restaurant — see source_crawl.py's `_crawl_and_store` for
    the identical reasoning one stage further down the chain."""
    from apps.api.app.services.manual_source_verification import (
        ManualSourceRejectedError,
        verify_and_persist_manual_source,
    )

    session_factory = get_sessionmaker()
    restaurant_id = uuid.uuid5(uuid.NAMESPACE_URL, f"hungrx:restaurant-seed:{restaurant_seed_id}")

    async with session_factory() as session:
        try:
            source = await verify_and_persist_manual_source(
                session, restaurant_id=restaurant_id, raw_url=menu_url
            )
        except ManualSourceRejectedError as exc:
            await session.commit()
            return {"status": "rejected", "restaurant_id": str(restaurant_id), "reason": str(exc)}
        await session.commit()

    return {
        "status": "verified",
        "restaurant_id": str(restaurant_id),
        "source_id": str(source.id),
        "source_url": source.url,
    }


async def _run(
    *,
    restaurant_seed_id: str,
    name: str,
    menu_url: str,
    city: str | None,
    state: str | None,
    country: str | None,
    phone: str | None,
) -> dict[str, Any]:
    verify_outcome = await _verify_only(restaurant_seed_id=restaurant_seed_id, menu_url=menu_url)
    if verify_outcome["status"] != "verified":
        return verify_outcome

    restaurant_id = verify_outcome["restaurant_id"]
    source_id = verify_outcome["source_id"]
    source_url = verify_outcome["source_url"]

    from infrastructure.queue.queues import QUEUE_SOURCE_CRAWL, get_queue

    queue = get_queue(QUEUE_SOURCE_CRAWL)
    from apps.worker.app.jobs.source_crawl import run_source_crawl

    enqueued = queue.enqueue(
        run_source_crawl,
        restaurant_id=restaurant_id,
        source_url=source_url,
        source_id=source_id,
    )
    return {
        "status": "verified",
        "restaurant_id": restaurant_id,
        "source_id": source_id,
        "source_crawl_job_id": enqueued.id,
    }


def run_manual_restaurant_ingestion(
    *,
    restaurant_seed_id: str,
    name: str,
    menu_url: str,
    city: str | None = None,
    state: str | None = None,
    country: str | None = None,
    phone: str | None = None,
) -> dict[str, Any]:
    """Same job_type lock name ("restaurant_ingestion") as
    restaurant_ingestion.py's run_restaurant_ingestion — a manual and a
    search-based ingestion for the same restaurant_seed_id still can't
    race each other."""
    configure_logging(get_settings())

    log_started(job_id="inline", job_type=JOB_TYPE, restaurant_id=None, restaurant_seed_id=restaurant_seed_id)

    try:
        with RestaurantJobLock(get_redis_connection(), restaurant_id=restaurant_seed_id, job_type=JOB_TYPE):
            from infrastructure.queue.async_bridge import run_async

            result = run_async(
                _run,
                restaurant_seed_id=restaurant_seed_id,
                name=name,
                menu_url=menu_url,
                city=city,
                state=state,
                country=country,
                phone=phone,
            )
    except JobAlreadyRunningError as exc:
        log_skipped(job_id="inline", job_type=JOB_TYPE, restaurant_id=None, reason=str(exc))
        raise
    except Exception as exc:
        log_failed(job_id="inline", job_type=JOB_TYPE, restaurant_id=None, error=str(exc))
        raise

    log_completed(job_id="inline", job_type=JOB_TYPE, **result)
    return result
