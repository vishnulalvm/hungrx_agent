"""Named RQ queues for each job type this platform runs in the
background, plus the shared retry policy and dead-letter (RQ's own
`FailedJobRegistry`) access.

One named queue per job type (rather than one shared queue) so a worker
fleet can be scaled/prioritized per job type later (e.g. more workers on
`source_crawl` than `maintenance_polling`) without changing job code —
`apps/worker/app/main.py`'s default worker still listens on all of them.
"""

from rq import Queue
from rq.job import Retry
from rq.registry import FailedJobRegistry

from infrastructure.queue.redis_connection import get_redis_connection

QUEUE_RESTAURANT_INGESTION = "restaurant_ingestion"
QUEUE_SOURCE_CRAWL = "source_crawl"
QUEUE_COLLECTOR_WORKFLOW = "collector_workflow"
QUEUE_MAINTENANCE_POLLING = "maintenance_polling"
QUEUE_REVIEWER_WORKFLOW = "reviewer_workflow"

ALL_QUEUE_NAMES = [
    QUEUE_RESTAURANT_INGESTION,
    QUEUE_SOURCE_CRAWL,
    QUEUE_COLLECTOR_WORKFLOW,
    QUEUE_MAINTENANCE_POLLING,
    QUEUE_REVIEWER_WORKFLOW,
]

# 3 attempts total (1 original + 2 retries), backing off 30s / 120s so a
# transient failure (a flaky fetch, a momentary DB hiccup) gets a couple
# of spaced-out chances before landing in the dead-letter registry.
# Applied uniformly across job types — none of these jobs has a
# different retryability profile from the others.
DEFAULT_RETRY = Retry(max=2, interval=[30, 120])


def get_queue(name: str) -> Queue:
    return Queue(name, connection=get_redis_connection())


def all_queues() -> list[Queue]:
    return [get_queue(name) for name in ALL_QUEUE_NAMES]


def failed_job_registry(queue_name: str) -> FailedJobRegistry:
    """RQ's dead-letter equivalent: jobs that exhausted DEFAULT_RETRY
    land here rather than vanishing, so an operator (or an alert) can
    inspect/requeue them. `queue.failed_job_registry` on any Queue
    instance gives the same thing; this is just a named entry point."""
    return get_queue(queue_name).failed_job_registry
