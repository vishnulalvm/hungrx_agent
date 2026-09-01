"""Structured job logging: one JSON log line per job lifecycle event
(started/completed/failed/skipped), always carrying the same
correlation fields (job_id, job_type, restaurant_id, agent_run_id) so a
log aggregator can trace one restaurant's activity across every job type
it touched. core.config.logging.JsonFormatter's field allowlist doesn't
cover these job-specific fields, so the payload is embedded as a JSON
string in the log message itself rather than via `extra=`.
"""

import json
import logging

logger = logging.getLogger("hungrx.worker.jobs")


def _log(level: int, event: str, *, job_id: str, job_type: str, restaurant_id: str | None, **fields) -> None:
    payload = {
        "event": event,
        "job_id": job_id,
        "job_type": job_type,
        "restaurant_id": restaurant_id,
        **fields,
    }
    logger.log(level, json.dumps(payload, default=str))


def log_started(*, job_id: str, job_type: str, restaurant_id: str | None, **fields) -> None:
    _log(logging.INFO, "job.started", job_id=job_id, job_type=job_type, restaurant_id=restaurant_id, **fields)


def log_completed(*, job_id: str, job_type: str, restaurant_id: str | None, **fields) -> None:
    _log(logging.INFO, "job.completed", job_id=job_id, job_type=job_type, restaurant_id=restaurant_id, **fields)


def log_skipped(*, job_id: str, job_type: str, restaurant_id: str | None, reason: str, **fields) -> None:
    _log(
        logging.INFO,
        "job.skipped",
        job_id=job_id,
        job_type=job_type,
        restaurant_id=restaurant_id,
        reason=reason,
        **fields,
    )


def log_failed(*, job_id: str, job_type: str, restaurant_id: str | None, error: str, **fields) -> None:
    _log(
        logging.ERROR,
        "job.failed",
        job_id=job_id,
        job_type=job_type,
        restaurant_id=restaurant_id,
        error=error,
        **fields,
    )
