"""Job status lookup: RQ already tracks each job's own lifecycle status
(queued/started/finished/failed) keyed by RQ job id; this module is the
one place that also resolves the AgentRun a workflow job produced, so a
caller checking "how did this job go" for a collector_workflow/
reviewer_workflow job gets both — the queue-level outcome and the
graph-level run it produced, without needing to know the graph's
agent_run_id is a distinct id from the RQ job id that enqueued it (the
job only learns its own agent_run_id after the graph creates one, part
way through the run — see workflows/*/nodes/*_authority.py /
*_hash_polling.py).
"""

from typing import Any

from rq.exceptions import NoSuchJobError
from rq.job import Job, JobStatus

from infrastructure.queue.redis_connection import get_redis_connection


def get_job_status(job_id: str) -> dict[str, Any] | None:
    """Returns RQ's own status plus (for a finished job whose return
    value included one) the agent_run_id it produced. None if no job
    with this id exists (expired result, or never enqueued)."""
    try:
        job = Job.fetch(job_id, connection=get_redis_connection())
    except NoSuchJobError:
        return None

    status: JobStatus | None = job.get_status(refresh=True)
    result = job.return_value() if status == JobStatus.FINISHED else None
    latest_result = job.latest_result()

    return {
        "job_id": job_id,
        "status": status.value if status else None,
        "enqueued_at": job.enqueued_at,
        "started_at": job.started_at,
        "ended_at": job.ended_at,
        "agent_run_id": result.get("agent_run_id") if isinstance(result, dict) else None,
        "exc_info": latest_result.exc_string if latest_result else None,
    }
