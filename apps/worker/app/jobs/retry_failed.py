"""Job: retryable failures.

Every job type above already retries automatically via RQ's own `Retry`
policy (infrastructure/queue/queues.py's DEFAULT_RETRY — 2 retries,
backing off 30s/120s) before a job is ever considered dead. This module
is the layer above that: a periodic sweep of each queue's
FailedJobRegistry (RQ's dead-letter store — jobs that exhausted their
retries land here rather than vanishing) that requeues anything whose
failure looks transient (a connection error, a timeout) and leaves
anything else (a validation error, a domain rejection) alone for a human
to look at, since blindly re-running a job that will deterministically
fail the same way again just wastes compute and delays noticing a real
bug.

Not itself locked per-restaurant — this operates on job ids already
sitting in the dead-letter registry, and re-enqueueing one goes through
the same job-type-specific RestaurantJobLock the original job used, so a
requeue of a dead job for a restaurant that's since started a fresh run
the normal way is skipped the same way any other duplicate would be.
"""

import logging
from typing import Any

from rq import get_current_job
from rq.job import Job

from apps.worker.app.jobs.logging import log_completed, log_started
from core.config.logging import configure_logging
from core.config.settings import get_settings
from infrastructure.queue.queues import ALL_QUEUE_NAMES, failed_job_registry

logger = logging.getLogger("hungrx.worker.jobs.retry_failed")

JOB_TYPE = "retry_failed"

# Substrings of an exception's class name that mark a failure as
# transient (network/infra hiccups) rather than a deterministic bug in
# the job's own logic — only these get auto-requeued.
_TRANSIENT_EXC_MARKERS = (
    "ConnectionError",
    "TimeoutError",
    "ConnectionRefusedError",
    "OperationalError",  # SQLAlchemy: DB connection drop/pool exhaustion
)


def _looks_transient(exc_info: str | None) -> bool:
    if not exc_info:
        return False
    return any(marker in exc_info for marker in _TRANSIENT_EXC_MARKERS)


def _sweep_registry(queue_name: str) -> dict[str, Any]:
    registry = failed_job_registry(queue_name)
    requeued: list[str] = []
    left_for_review: list[str] = []

    for job_id in registry.get_job_ids():
        job: Job | None = registry.job_class.fetch(job_id, connection=registry.connection)
        if job is None:
            continue
        if _looks_transient(job.exc_info):
            registry.requeue(job_id)
            requeued.append(job_id)
            logger.info("retry_failed: requeued %s (%s) — looked transient", job_id, queue_name)
        else:
            left_for_review.append(job_id)

    return {"queue": queue_name, "requeued": requeued, "left_for_review": left_for_review}


def run_retry_failed() -> dict[str, Any]:
    """RQ entry point. Intended to be triggered on a schedule, same as
    run_maintenance_polling — sweeps every queue's FailedJobRegistry."""
    configure_logging(get_settings())
    job = get_current_job()
    job_id = job.id if job else "sync"

    log_started(job_id=job_id, job_type=JOB_TYPE, restaurant_id=None)

    per_queue = [_sweep_registry(name) for name in ALL_QUEUE_NAMES]
    result = {
        "total_requeued": sum(len(entry["requeued"]) for entry in per_queue),
        "total_left_for_review": sum(len(entry["left_for_review"]) for entry in per_queue),
        "per_queue": per_queue,
    }

    log_completed(job_id=job_id, job_type=JOB_TYPE, restaurant_id=None, **result)
    return result
