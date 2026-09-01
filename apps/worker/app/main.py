"""Background job worker entry point.

Starts one RQ Worker listening on every named queue
(infrastructure/queue/queues.py's ALL_QUEUE_NAMES) — restaurant
ingestion, source crawling, the collector workflow, maintenance polling,
the reviewer workflow, and retryable-failure sweeps. A single worker
process handling every queue is the right default for this stage (low
volume, one container under docker-compose's `restart: unless-stopped`);
splitting into per-queue worker processes later needs no code change
here, just running this same module with a narrower queue list.
"""

from core.config.logging import configure_logging
from core.config.settings import get_settings
from infrastructure.queue.queues import all_queues
from infrastructure.queue.redis_connection import get_redis_connection


def main() -> None:
    settings = get_settings()
    configure_logging(settings)

    from rq import Worker

    worker = Worker(all_queues(), connection=get_redis_connection())
    worker.work(with_scheduler=True)


if __name__ == "__main__":
    main()
