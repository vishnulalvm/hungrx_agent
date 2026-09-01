"""Job: maintenance polling.

The scheduler CLAUDE.md's Status section calls out as "not yet built":
periodically sweeps every published restaurant and enqueues one
`reviewer_workflow` job per restaurant to check its source for drift.
Intentionally cheap at this layer — it does no hashing/fetching itself
(that's Temporal Hash Polling's job, the reviewer graph's own first
node); this job's only work is "who needs checking" and "hand each one
off," so a maintenance sweep never blocks on N restaurants' worth of
network I/O.

Idempotency / no duplicate work: does not itself lock per restaurant —
enqueueing a reviewer_workflow job is cheap and safe to attempt
repeatedly (RQ just queues another job), but `run_reviewer_workflow`
below is what actually holds the per-restaurant lock, so if restaurant
X's previous reviewer run hasn't finished yet, its queued job will raise
JobAlreadyRunningError and skip cleanly rather than run concurrently.
This sweep itself is guarded by its own lock (job_type="maintenance_polling",
keyed by a fixed sentinel id) purely to stop two scheduler firings from
enumerating and enqueueing the same sweep twice if they overlap.
"""

from typing import Any

from rq import get_current_job

from apps.worker.app.jobs.logging import log_completed, log_failed, log_skipped, log_started
from core.config.logging import configure_logging
from core.config.settings import get_settings
from database.session import get_sessionmaker
from infrastructure.queue.async_bridge import run_async
from infrastructure.queue.lock import JobAlreadyRunningError, RestaurantJobLock
from infrastructure.queue.queues import QUEUE_REVIEWER_WORKFLOW, get_queue
from infrastructure.queue.redis_connection import get_redis_connection

JOB_TYPE = "maintenance_polling"
_SWEEP_LOCK_SENTINEL = "all-restaurants-sweep"


async def _run() -> dict[str, Any]:
    from database.repositories.restaurant_repository import RestaurantRepository

    session_factory = get_sessionmaker()
    async with session_factory() as session:
        restaurant_ids = await RestaurantRepository(session).list_ids()

    queue = get_queue(QUEUE_REVIEWER_WORKFLOW)
    from apps.worker.app.jobs.reviewer_workflow import run_reviewer_workflow

    enqueued_job_ids = []
    for restaurant_id in restaurant_ids:
        enqueued = queue.enqueue(run_reviewer_workflow, restaurant_id=str(restaurant_id))
        enqueued_job_ids.append(enqueued.id)

    return {"restaurants_swept": len(restaurant_ids), "enqueued_job_ids": enqueued_job_ids}


def run_maintenance_polling() -> dict[str, Any]:
    """RQ entry point. No parameters — sweeps every published
    restaurant. Intended to be triggered on a schedule (e.g. RQ
    Scheduler / cron enqueueing this job periodically), not by request."""
    configure_logging(get_settings())
    job = get_current_job()
    job_id = job.id if job else "sync"

    log_started(job_id=job_id, job_type=JOB_TYPE, restaurant_id=None)

    try:
        with RestaurantJobLock(get_redis_connection(), restaurant_id=_SWEEP_LOCK_SENTINEL, job_type=JOB_TYPE):
            result = run_async(_run)
    except JobAlreadyRunningError as exc:
        log_skipped(job_id=job_id, job_type=JOB_TYPE, restaurant_id=None, reason=str(exc))
        raise
    except Exception as exc:
        log_failed(job_id=job_id, job_type=JOB_TYPE, restaurant_id=None, error=str(exc))
        raise

    log_completed(job_id=job_id, job_type=JOB_TYPE, restaurant_id=None, **result)
    return result
